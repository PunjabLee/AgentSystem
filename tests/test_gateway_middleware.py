"""Gateway 中间件的语义钉子（P2.3.1）。

这些用例钉住的是**框架行为**与**默认安全**两类性质，它们的共同点是
「违反后不报错」：contextvar 没传下去，db 层会抛 LookupError（还算响）；
但 contextvar 跨请求泄漏、某个路径漏了鉴权，两者都悄无声息。
"""

import asyncio
import os
import pathlib
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

from agentsystem.api.app import create_app
from agentsystem.gateway.context import current_context
from agentsystem.gateway.identity import get_identity_registry
from agentsystem.gateway.middleware import PUBLIC_PATHS
from agentsystem.gateway.ratelimit import ConcurrencyGate


def _token() -> str:
    """取集团质量部的演示令牌（三个 BU 全授权，便于观察范围）。"""
    token = os.environ.get("DEMO_TOKEN_GROUP")
    if not token:
        pytest.skip("DEMO_TOKEN_GROUP 未配置")
    return token


@pytest.fixture
def app_with_probe():
    """装一个回显上下文的探针路由。

    探针只存在于测试里 —— 生产镜像里不该有一个把 user_id 和权限范围原样吐出来
    的端点，那等于给越权探测提供了一面镜子。
    """
    app = create_app(concurrency_limit=64)

    @app.get("/probe/ctx")
    async def probe_ctx() -> dict[str, object]:
        ctx = current_context()
        return {
            "user_id": ctx.user_id,
            "trace_id": ctx.trace_id,
            "bu_codes": sorted(ctx.bu_codes),
        }

    return app


@asynccontextmanager
async def _client(app):
    """一个带已领取会话标识的客户端。

    必须先 ``POST /v1/sessions`` 换一个 conversation_id —— 服务端不接受
    客户端自造的取值，所以测试也没法图省事写死一个。
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", timeout=30) as c:
        r = await c.post("/v1/sessions", headers={"Authorization": f"Bearer {_token()}"})
        assert r.status_code == 200, r.text
        c.headers.update(
            {
                "Authorization": f"Bearer {_token()}",
                "X-Conversation-Id": r.json()["conversation_id"],
            }
        )
        yield c


async def test_context_reaches_the_endpoint(app_with_probe) -> None:
    """中间件设的 contextvar 必须能被端点读到。

    这是 db 层权限注入成立的前提 —— ``scoped_select`` 就是从这里取范围的。
    """
    async with _client(app_with_probe) as c:
        body = (await c.get("/probe/ctx")).json()
    # 期望值从 users.yaml 反查而不是写死：写死的话改一次配置就得改一次测试，
    # 而这个用例要验的是「令牌解析成了对的那个人」，不是「那个人叫什么」。
    expected = get_identity_registry().resolve(_token())
    assert body["user_id"] == expected.user_id
    assert body["bu_codes"] == sorted(expected.bu_codes)
    assert len(expected.bu_codes) == 3, "本用例特意选跨 BU 用户，以便看出范围是否完整传下"


async def test_context_does_not_leak_between_requests(app_with_probe) -> None:
    """两次请求的 trace_id 必须不同，且第二次不得看见第一次的上下文。

    泄漏在单人手测时永远发现不了：一个人顺序发请求，上一个的身份恰好也是
    自己的。它只在多用户并发时才现形，而那时已经是线上了。
    """
    async with _client(app_with_probe) as c:
        first = (await c.get("/probe/ctx")).json()
        second = (await c.get("/probe/ctx")).json()
    assert first["trace_id"] != second["trace_id"]


async def test_context_is_cleared_after_the_request(app_with_probe) -> None:
    """请求结束后当前上下文必须已还原 —— 这是 ``finally: reset`` 的验证点。

    ``BaseHTTPMiddleware`` 在这里会过不去：它把下游放进子任务，子任务里的
    set 不会被父 context 的 reset 还原。用例转红即说明有人把中间件换了写法。
    """
    async with _client(app_with_probe) as c:
        await c.get("/probe/ctx")
    with pytest.raises(LookupError):
        current_context()


@pytest.mark.parametrize("path", sorted(PUBLIC_PATHS))
async def test_public_paths_need_no_token(app_with_probe, path: str) -> None:
    """豁免名单上的路径不带令牌也必须可达。

    健康检查若需要令牌，「服务活着吗」与「令牌配对吗」就纠缠在一起；
    schema 端点若需要令牌，P2.3.6 的 Dify 导入探针会在第一步就卡住。
    """
    async with AsyncClient(transport=ASGITransport(app=app_with_probe), base_url="http://t") as c:
        assert (await c.get(path)).status_code != 403


async def test_unlisted_path_requires_token(app_with_probe) -> None:
    """名单外的路径一律要令牌 —— 包括不存在的路径。

    不存在的路径也要先鉴权，是为了不让未认证者拿 404/403 的差别去**探测路由表**。
    """
    async with AsyncClient(transport=ASGITransport(app=app_with_probe), base_url="http://t") as c:
        for path in ("/probe/ctx", "/v1/orders", "/definitely/not/a/route"):
            r = await c.get(path)
            assert r.status_code == 403, path
            assert r.json()["code"] == "AUTH_TOKEN_MISSING"


async def test_client_supplied_conversation_id_is_rejected(app_with_probe) -> None:
    """客户端自造的 conversation_id 必须被拒（P1 §2.5.3）。

    放过它会串话：两段无关会话派生出同一个 session_id，会话 A 建的待确认写操作
    会出现在会话 B 里 —— 用户可能确认了他没打算确认的那一单。
    """
    async with AsyncClient(transport=ASGITransport(app=app_with_probe), base_url="http://t") as c:
        r = await c.get(
            "/probe/ctx",
            headers={"Authorization": f"Bearer {_token()}", "X-Conversation-Id": "1"},
        )
        assert r.status_code == 403
        assert r.json()["code"] == "AUTH_BAD_CONVERSATION_ID"


async def test_missing_conversation_header_is_rejected(app_with_probe) -> None:
    """令牌有效但完全不带 X-Conversation-Id，也必须被拒。

    这条用例是变异测试补出来的：原先只测了「传了一个自造的取值」，
    把「缺失」这条分支删掉，整个用例集照样全绿 —— 于是一个没有会话绑定的
    请求可以直达端点，write_intent 的会话归属就无从谈起了。
    """
    async with AsyncClient(transport=ASGITransport(app=app_with_probe), base_url="http://t") as c:
        r = await c.get("/probe/ctx", headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 403
    assert r.json()["code"] == "AUTH_NO_CONVERSATION"


async def test_every_error_carries_the_envelope(app_with_probe) -> None:
    """任何错误路径都必须带 ``retryable`` —— 包括框架自己产生的 404。

    没有这一条，走错路径的调用会拿到 Starlette 默认的 ``{"detail": ...}``，
    P4 的降级判定读不到 ``retryable`` 就只能猜。而 404 恰恰是模型最容易撞上的
    错误：工具名写错、路径拼错都落在这里。
    """
    async with _client(app_with_probe) as c:
        r = await c.get("/definitely/not/a/route")
    assert r.status_code == 404
    body = r.json()
    assert set(body) >= {"code", "message", "retryable", "trace_id"}
    assert body["retryable"] is False


async def test_trace_id_is_echoed_in_the_response_header(app_with_probe) -> None:
    """响应头里的 trace_id 必须与上下文里的一致。

    它是用户报障时手里唯一能对上审计表的串；对不上就等于没有。
    """
    async with _client(app_with_probe) as c:
        r = await c.get("/probe/ctx")
    assert r.headers["x-trace-id"] == r.json()["trace_id"]


async def test_gate_rejects_with_busy_not_with_a_business_error() -> None:
    """闸门满员时回 429 / ``SYS_BUSY`` 且 ``retryable=true``。

    不可重试的业务拒绝与可重试的容量问题必须分开：P4 拿 ``retryable``
    决定要不要上 RPA 兜底，把「挤满了」判成业务拒绝会让降级永远不触发。
    """
    app = create_app(concurrency_limit=1)
    started = asyncio.Event()
    release = asyncio.Event()

    @app.get("/probe/slow")
    async def slow() -> dict[str, bool]:
        started.set()
        await release.wait()
        return {"ok": True}

    async with _client(app) as c:
        first = asyncio.create_task(c.get("/probe/slow"))
        await asyncio.wait_for(started.wait(), timeout=5)
        second = await c.get("/probe/slow")  # 闸门此刻已满
        release.set()
        await first
    assert second.status_code == 429
    assert second.json()["code"] == "SYS_BUSY"
    assert second.json()["retryable"] is True
    assert second.headers["retry-after"] == "1"


async def test_gate_releases_its_slot_on_failure() -> None:
    """端点抛异常时闸门名额必须归还 —— 否则每失败一次闸门就永久小一格。

    这类泄漏在演示中表现为「用着用着就一直 429」，而日志里只有最初那几条
    业务报错，看不出关联。
    """
    gate = ConcurrencyGate(limit=2)
    for _ in range(10):
        assert (await gate.acquire()).allowed
        try:
            raise RuntimeError("端点炸了")
        except RuntimeError:
            pass
        finally:
            await gate.release()
    assert gate.in_flight == 0


def test_dotenv_is_loaded() -> None:
    """确保 .env 存在 —— 上面的用例全部依赖演示令牌。"""
    assert pathlib.Path(".env").exists() or os.environ.get("DEMO_TOKEN_GROUP")
