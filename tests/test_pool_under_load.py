"""连接池在真实请求路径下的负载验证（OPEN-ITEMS 2.1）。

## 为什么这组用例非有不可

此前「共池会死锁」那次实测用的是**裸引擎**，不经 FastAPI、不经中间件。
业务池 5 / 审计池 3 且 ``max_overflow=0`` 这两个数字，在 P2.3.1 落地之前
**从未受过真实请求路径的检验**。三位评审在这一点上独立收敛。

## 实测结论（完整数据见 docs/measurements/pool-under-load.md）

* 300 个真实业务查询并发打进 5 条连接，0.28 秒全部完成 —— 池子不是瓶颈。
* 60 个各占 1 秒的请求，最慢的等了 12.2 秒且**返回 200**，与「大模型慢」
  无法区分。这是 ``pool_timeout`` 从 30 秒压到 5 秒的直接理由。
"""

import asyncio
import os
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from agentsystem.api.app import create_app
from agentsystem.db import session as db_session
from agentsystem.db.session import _APP_POOL_SIZE, app_session, dispose_engines
from agentsystem.gateway.ratelimit import TooManyRequests

#: 并发倍数。取 10 倍池大小：既明显超出池容量，又不至于让 CI 跑太久。
OVERSUBSCRIBE = 10


def _token() -> str:
    """演示令牌。"""
    token = os.environ.get("DEMO_TOKEN_GROUP")
    if not token:
        pytest.skip("DEMO_TOKEN_GROUP 未配置")
    return token


def _app_with_db_probe(hold_s: float | None = None):
    """装一个真的触库的探针端点。

    必须**真的取一条连接**，否则测的就不是连接池 —— 一个只返回常量的端点
    再怎么并发也压不到池子上。
    """
    app = create_app()

    @app.get("/probe/db")
    async def probe() -> dict[str, bool]:
        async with app_session() as s:
            if hold_s is None:
                await s.execute(text("SELECT 1"))
            else:
                # pg_sleep 占住连接，模拟慢查询 —— 这是唯一能让 5 条连接
                # 真正告急的办法，真实查询快到压不出排队。
                await s.execute(text("SELECT pg_sleep(:h)"), {"h": hold_s})
        return {"ok": True}

    return app


@asynccontextmanager
async def _client(app):
    """带已领取会话标识的客户端。"""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t", timeout=60
    ) as c:
        r = await c.post("/v1/sessions", headers={"Authorization": f"Bearer {_token()}"})
        c.headers.update(
            {
                "Authorization": f"Bearer {_token()}",
                "X-Conversation-Id": r.json()["conversation_id"],
            }
        )
        yield c


async def test_pool_timeout_is_not_left_at_the_silent_default() -> None:
    """``pool_timeout`` 必须显式设置且远小于 30 秒。

    SQLAlchemy 的默认值是 30 秒**静默排队**。本项目每个请求背后都可能有一次
    LLM 调用，一个在池上默默等 30 秒的请求从外部看就是「今天模型慢」——
    这种混淆会一路带进 P5 的延迟评测。

    用例直接读引擎的实际配置，不读常量：常量对了而没传进 ``create_async_engine``
    是个真实可能犯的错，而那种错误没有任何征兆。
    """
    from agentsystem.db.session import get_app_engine

    assert get_app_engine().pool._timeout <= 10.0, "pool_timeout 过大，排队会伪装成模型延迟"


async def test_oversubscribed_pool_does_not_deadlock_through_the_request_path() -> None:
    """并发远超池大小时，请求全部成功而不是互相卡死。

    这是 OPEN-ITEMS 2.1 要的那个验证：此前的死锁实测走的是裸引擎，
    没有中间件、没有 contextvar、没有闸门。这里走完整路径。

    断言「全部 200」而非「大部分 200」—— 业务池与审计池独立之后，
    排队只该变慢，不该变成失败。出现任何 5xx 都说明隔离没做到。
    """
    n = _APP_POOL_SIZE * OVERSUBSCRIBE
    app = _app_with_db_probe()
    async with _client(app) as c:
        results = await asyncio.gather(*[c.get("/probe/db") for _ in range(n)])
    codes = sorted({r.status_code for r in results})
    assert codes == [200], f"并发 {n} 时出现非 200：{codes}"


async def test_pool_exhaustion_surfaces_as_busy_not_as_internal_error() -> None:
    """等不到连接时必须报 ``SYS_BUSY``（429），不是 ``SYS_INTERNAL``（500）。

    不转译的话它是一条裸 ``sqlalchemy.exc.TimeoutError``，落进兜底处理器变成
    「本系统内部故障」。两者的 ``retryable`` 碰巧都是 true，但 P4 的降级逻辑
    对系统故障会上 RPA 兜底 —— 而连接池挤一挤过会儿就空了，不该惊动 RPA。

    用例把池压到 1 条连接、超时压到 0.2 秒，让拥塞在毫秒级发生；
    否则钉这条性质要等 5 秒。
    """
    orig_size, orig_timeout = db_session._APP_POOL_SIZE, db_session._POOL_TIMEOUT_S
    await dispose_engines()
    db_session._APP_POOL_SIZE, db_session._POOL_TIMEOUT_S = 1, 0.2
    try:
        app = _app_with_db_probe(hold_s=1.0)
        async with _client(app) as c:
            results = await asyncio.gather(*[c.get("/probe/db") for _ in range(4)])
        busy = [r for r in results if r.status_code == 429]
        assert busy, f"池只剩 1 条连接仍无人被拒，状态码={[r.status_code for r in results]}"
        assert all(r.json()["code"] == "SYS_BUSY" for r in busy)
        assert all(r.json()["retryable"] is True for r in busy)
        assert not [r for r in results if r.status_code >= 500], "容量问题不得报成 5xx"
    finally:
        db_session._APP_POOL_SIZE, db_session._POOL_TIMEOUT_S = orig_size, orig_timeout
        await dispose_engines()


def test_pool_timeout_translation_is_wired_to_the_right_exception() -> None:
    """转译的目标类型必须是可重试的 429。

    单独钉一条：若日后有人把 ``TooManyRequests`` 的基类改成
    ``BusinessRejection``，上面的集成用例仍会看到 429，但 ``retryable``
    会变成 false —— 降级判定从此永不触发，且没有任何报错。
    """
    exc = TooManyRequests("x")
    assert exc.http_status == 429
    assert exc.retryable is True
