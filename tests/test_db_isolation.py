"""库级隔离的断言（P1.3.5 四角色 · 宪法第三条）。

两个方向都要测，缺一个都不算隔离：

* 别人连不进 ``agentsystem`` —— 审计表的三层防护才有意义，否则绕过第一层
  只需要换个角色登录。
* ``app_ro`` 写不了 ``dify`` —— 宪法第三条「向量库写入权归 Dify 独占」的
  机械保障。只读角色若能写，"独占"就只是文档里的一句话。
"""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from agentsystem.settings import get_settings


def _dsn(user: str, password: str, database: str) -> str:
    """按角色与库名拼连接串。不走 Settings.dsn —— 它固定连 pg_database。"""
    s = get_settings()
    return f"postgresql+psycopg://{user}:{password}@{s.pg_host}:{s.pg_port}/{database}"


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} 未配置")
    return value


def test_dify_owner_cannot_connect_to_agentsystem() -> None:
    """🔴 Dify 专用角色对 agentsystem 无 CONNECT。

    这一条堵的是「绕过审计防护最省事的路径」：不必拆触发器，换个能连库的
    角色登录即可。REVOKE 写成显式的，不是靠"没授予"。
    """
    engine = create_engine(_dsn("dify_owner", _env("PG_DIFY_OWNER_PASSWORD"), "agentsystem"))
    with pytest.raises(OperationalError) as e:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    assert "permission denied" in str(e.value).lower()
    engine.dispose()


def test_dify_owner_can_connect_to_dify() -> None:
    """阳性对照：隔离不能把 Dify 自己也锁在门外。"""
    engine = create_engine(_dsn("dify_owner", _env("PG_DIFY_OWNER_PASSWORD"), "dify"))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
    engine.dispose()


@pytest.mark.parametrize("role_env", ["PG_MIGRATOR_PASSWORD", "PG_APP_PASSWORD", "PG_RO_PASSWORD"])
def test_business_roles_still_reach_agentsystem(role_env: str) -> None:
    """回归：收回 PUBLIC 的 CONNECT 后，三个业务角色仍需能连。

    没有这条，一次"顺手加强隔离"就会把自己锁死，且要到下次部署才发现。
    """
    user = {
        "PG_MIGRATOR_PASSWORD": "app_migrator",
        "PG_APP_PASSWORD": "app_rw",
        "PG_RO_PASSWORD": "app_ro",
    }[role_env]
    engine = create_engine(_dsn(user, _env(role_env), "agentsystem"))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
    engine.dispose()


def test_app_ro_cannot_write_to_dify() -> None:
    """🔴 宪法第三条：向量库写入权归 Dify 独占。

    app_ro 对 dify 库是只读。若只读角色能写，"独占"就只是文档里的一句话 ——
    而 Dify 的切块与向量一旦被外部写入，检索结果的可溯源性直接崩掉
    （宪法第九条）。
    """
    engine = create_engine(_dsn("app_ro", _env("PG_RO_PASSWORD"), "dify"))
    with pytest.raises((ProgrammingError, OperationalError)) as e:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE poc_should_not_exist (id int)"))
    assert "permission denied" in str(e.value).lower()
    engine.dispose()
