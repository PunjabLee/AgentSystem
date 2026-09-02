"""pytest 公共夹具。

这些测试直连本地 PostgreSQL，属于集成测试而非单元测试 —— P1.3.4 要验证的
是数据库自身的权限与触发器行为，用 mock 验证等于什么都没验证。
"""

from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession

from agentsystem.settings import get_settings

# 探针写入用的固定 trace_id。审计表不可删，这些行会永久留存 —— 这是
# append-only 的正确行为，不是泄漏。用固定前缀便于人工辨识。
PROBE_TRACE_ID = "p134-tamper-probe"


def _engine(role: str) -> Engine:
    """按角色建同步引擎（测试用，走 psycopg）。"""
    return create_engine(get_settings().dsn(role, driver="sync"), poolclass=None)


@pytest.fixture(scope="session")
def rw_engine() -> Iterator[Engine]:
    """app_rw —— 运行时业务账号，第一层 REVOKE 的作用对象。"""
    eng = _engine("app")
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def owner_engine() -> Iterator[Engine]:
    """app_migrator —— 表属主，REVOKE 对它无效，由第二/三层负责。"""
    eng = _engine("migrator")
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def seeded_audit_row(rw_engine: Engine) -> str:
    """确保 audit_log 至少有一行，再跑 UPDATE/DELETE 探针。

    这条夹具存在的理由是一次真实的假阴性：行触发器是 FOR EACH ROW，
    空表上的 DELETE 匹配 0 行、触发器根本不触发、语句静默成功 ——
    此时测试会"通过"，但什么都没验证。必须先播种。
    """
    with rw_engine.begin() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM audit_log WHERE trace_id = :t LIMIT 1"),
            {"t": PROBE_TRACE_ID},
        ).scalar()
        if not exists:
            conn.execute(
                text(
                    "INSERT INTO audit_log "
                    "(phase, user_id, action_type, trace_id, session_id, source, tool_name) "
                    "VALUES ('attempt', 'probe-user', 'read', :t, "
                    "'probe-sess', 'test', 'p134-seed')"
                ),
                {"t": PROBE_TRACE_ID},
            )
    return PROBE_TRACE_ID


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """一个自动回滚的业务会话。

    每个用例包在一个事务里、结束即回滚 —— write_intent 无防篡改约束，
    本可以删数据收尾，但回滚更彻底：连序列消耗都不留。
    """
    from agentsystem.db.session import get_app_sessionmaker

    async with get_app_sessionmaker()() as session:
        await session.begin()
        try:
            yield session
        finally:
            await session.rollback()


@pytest.fixture(scope="session", autouse=True)
def _dispose_engines_at_end() -> Iterator[None]:
    """收尾关闭连接池，避免 pytest 退出时报 event loop 已关闭。"""
    yield
    import asyncio

    from agentsystem.db.session import dispose_engines

    asyncio.run(dispose_engines())
