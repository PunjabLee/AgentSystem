"""异步数据库会话工厂（宪法第四条：全链路 async）。

两个独立引擎，不是冗余：

* ``app`` 引擎走 app_rw，承载全部业务读写。
* ``audit`` 引擎同样走 app_rw 但**连接池独立**——两段式审计的 attempt 行
  必须在业务事务之外提交（P1 详细设计 §3.2）。若共用池，业务事务耗尽连接
  时审计写入会排队等待，最终"操作发生了但没有 attempt 记录"，这恰是宪法
  第一条要防的情形。

驱动选择：业务侧一律 asyncpg。psycopg3 只用于 langgraph-checkpoint-postgres，
那是它的硬依赖，不在本模块管辖内。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agentsystem.settings import get_settings

#: 业务池大小。单人 PoC 且 max_connections 需与 Dify 的 14 个容器共享，
#: 故刻意保守；P1.1.6 会核算总量。
_APP_POOL_SIZE = 5
#: 审计池只承载短事务（一行 INSERT）。P1 详细设计 §3.2 定为 3，与之对齐。
_AUDIT_POOL_SIZE = 3
#: 等一条空闲连接的上限。**默认值是 30 秒，必须显式压低**（OPEN-ITEMS 2.1）。
#:
#: 实测（``docs/measurements/pool-under-load.md``）：60 个各占 1 秒的请求打进
#: 5 条连接的池子，最慢的一个等了 12.2 秒，且**返回 200** —— 从外部看与
#: 「今天大模型有点慢」无法区分。本项目每个请求背后都可能有一次 LLM 调用，
#: 这种混淆会一路带到 P5 的延迟评测里。
#:
#: 压到 5 秒后排队超时变成显式的 429，见 ``app_session`` 的转译。
_POOL_TIMEOUT_S = 5.0


def _make_engine(pool_size: int) -> AsyncEngine:
    """按池大小建异步引擎。"""
    return create_async_engine(
        get_settings().dsn("app", driver="asyncpg"),
        pool_size=pool_size,
        max_overflow=0,  # 不允许溢出：宁可排队暴露容量问题，也不悄悄突破核算
        pool_pre_ping=True,  # Dify 重启会掐断连接，预检避免首个请求必失败
        pool_timeout=_POOL_TIMEOUT_S,
    )


@lru_cache
def get_app_engine() -> AsyncEngine:
    """业务引擎（进程内单例）。"""
    return _make_engine(_APP_POOL_SIZE)


@lru_cache
def get_audit_engine() -> AsyncEngine:
    """审计引擎（进程内单例，连接池与业务隔离）。"""
    return _make_engine(_AUDIT_POOL_SIZE)


@lru_cache
def get_app_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """业务会话工厂。"""
    return async_sessionmaker(get_app_engine(), expire_on_commit=False)


@lru_cache
def get_audit_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """审计会话工厂。"""
    return async_sessionmaker(get_audit_engine(), expire_on_commit=False)


@asynccontextmanager
async def app_session() -> AsyncIterator[AsyncSession]:
    """业务会话上下文：正常提交，异常回滚。

    池排队超时被转译成 :class:`~agentsystem.gateway.ratelimit.TooManyRequests`。
    **不转译的话它是一条裸 ``sqlalchemy.exc.TimeoutError``**，落进兜底处理器变成
    ``SYS_INTERNAL`` / 500 —— 把「一时挤满了」报成了「本系统内部故障」。
    两者的 ``retryable`` 碰巧都是 true，但 P4 的降级逻辑读的不只是这一个字段：
    系统故障会触发 RPA 兜底，而连接池挤一挤过会儿就空了，根本不该惊动 RPA。

    Raises:
        TooManyRequests: 等不到空闲连接（``_POOL_TIMEOUT_S`` 秒内）。
    """
    # 导入放在函数内：ratelimit 属 gateway 层，db 层在模块顶层引它会形成
    # 「底层依赖上层」的方向倒置，也让 db 模块无法脱离 gateway 单独测试。
    from agentsystem.gateway.ratelimit import TooManyRequests

    try:
        async with get_app_sessionmaker()() as session, session.begin():
            yield session
    except PoolTimeout as exc:
        raise TooManyRequests(
            f"数据库连接繁忙，等待超过 {_POOL_TIMEOUT_S:.0f} 秒，请稍后重试"
        ) from exc


async def dispose_engines() -> None:
    """关闭全部连接池。仅供应用关停与测试收尾调用。"""
    for factory in (get_app_engine, get_audit_engine):
        if factory.cache_info().currsize:
            await factory().dispose()
    for f in (get_app_engine, get_audit_engine, get_app_sessionmaker, get_audit_sessionmaker):
        f.cache_clear()
