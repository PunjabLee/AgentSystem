"""身份与会话契约的断言（P1 详细设计 §2.5）。

三条红线 —— 宪法一的操作者、宪法十的范围上界、write_intent 的会话绑定
—— 都从 RequestContext 取值，故本层错一处就是三处越权。
"""

import os

import pytest

from agentsystem.errors import AuthError
from agentsystem.gateway.context import RequestContext
from agentsystem.gateway.identity import (
    IdentityRegistry,
    UserRecord,
    authenticate,
    derive_session_id,
    new_conversation_id,
)

#: 服务端签发的取值。多处用例复用同一个，以便断言 session_id 的稳定性。
CONV_A = new_conversation_id()


def _token(var: str) -> str:
    tok = os.environ.get(var)
    if not tok:
        pytest.skip(f"{var} 未在 .env 中配置")
    return tok


@pytest.fixture(scope="session")
def yr_token() -> str:
    """只授权 BU-A 的印染销售 —— 宪法第十条负测试的主角。"""
    return _token("DEMO_TOKEN_YR")


@pytest.fixture(scope="session")
def tile_token() -> str:
    return _token("DEMO_TOKEN_TILE")


# ── 令牌解析 ──────────────────────────────────────────────────
def test_valid_token_resolves_scope(yr_token: str) -> None:
    ctx = authenticate(yr_token, conversation_id=CONV_A)
    assert ctx.user_id == "u_yr_01"
    assert ctx.bu_codes == frozenset({"BU-A"})
    assert ctx.regions == frozenset({"华东", "华南"})


def test_invalid_token_rejected() -> None:
    with pytest.raises(AuthError) as e:
        authenticate("not-a-real-token", conversation_id=CONV_A)
    assert e.value.code == "AUTH_TOKEN_INVALID"
    assert e.value.http_status == 403


def test_duplicate_token_hash_is_refused() -> None:
    """两个用户共用一枚令牌 → 审计里的「操作者」不可判定，必须拒绝加载。"""
    same = "a" * 64
    with pytest.raises(ValueError, match="共用令牌"):
        IdentityRegistry(
            [
                UserRecord("u1", "甲", same, frozenset({"BU-A"}), frozenset({"*"})),
                UserRecord("u2", "乙", same, frozenset({"BU-B"}), frozenset({"*"})),
            ]
        )


# ── session_id 派生：本节是防冒充的核心 ───────────────────────
def test_session_id_is_stable_across_requests(yr_token: str) -> None:
    """同一用户同一会话必须稳定 —— 否则跨请求的确认流程接不上。"""
    a = authenticate(yr_token, conversation_id=CONV_A)
    b = authenticate(yr_token, conversation_id=CONV_A)
    assert a.session_id == b.session_id


def test_same_conversation_id_yields_different_session_per_user(
    yr_token: str, tile_token: str
) -> None:
    """🔴 照抄他人的 conversation_id 也落不到他人的 write_intent 上。

    这正是 §2.5.3 要求"session_id 服务端生成"的目的：客户端可指定即可
    冒充他人会话完成确认。HMAC 里压了已认证的 user_id，冒充失效。
    """
    a = authenticate(yr_token, conversation_id=CONV_A)
    b = authenticate(tile_token, conversation_id=CONV_A)
    assert a.user_id != b.user_id
    assert a.session_id != b.session_id


def test_session_id_not_derivable_without_key(yr_token: str) -> None:
    """客户端手里只有 conversation_id，算不出 session_id。"""
    ctx = authenticate(yr_token, conversation_id=CONV_A)
    assert ctx.session_id != "c-1"
    assert "c-1" not in ctx.session_id
    assert ctx.session_id != derive_session_id("u_tile_01", "c-1")


# ── trace_id ──────────────────────────────────────────────────
def test_trace_id_is_fresh_and_client_value_is_never_authoritative(yr_token: str) -> None:
    """入站 X-Trace-Id 只降级留存，绝不成为权威 trace_id。

    否则客户端可让两次操作共用一个 id，污染 attempt/outcome 的串联。
    """
    ctx = authenticate(yr_token, conversation_id=CONV_A, client_trace_id="client-supplied")
    assert ctx.client_trace_id == "client-supplied"
    assert ctx.trace_id != "client-supplied"
    other = authenticate(yr_token, conversation_id=CONV_A, client_trace_id="client-supplied")
    assert ctx.trace_id != other.trace_id, "每次请求必须是新的 trace_id"


def test_trace_id_is_uuid7_time_ordered(yr_token: str) -> None:
    """UUIDv7 自带时间前缀：按 trace_id 排序即近似时序，省一个索引。"""
    ids = [authenticate(yr_token, conversation_id=CONV_A).trace_id for _ in range(5)]
    assert ids == sorted(ids)


# ── 宪法第十条：范围收窄 ──────────────────────────────────────
def _ctx(bus: set[str], regions: set[str]) -> RequestContext:
    return RequestContext(
        user_id="u",
        session_id="s",
        trace_id="t",
        bu_codes=frozenset(bus),
        regions=frozenset(regions),
    )


def test_narrow_bu_unspecified_returns_full_grant() -> None:
    assert _ctx({"BU-A", "BU-B"}, {"*"}).narrow_bu(None) == frozenset({"BU-A", "BU-B"})


def test_narrow_bu_intersects() -> None:
    ctx = _ctx({"BU-A", "BU-B"}, {"*"})
    assert ctx.narrow_bu(frozenset({"BU-B", "BU-C"})) == frozenset({"BU-B"})


def test_narrow_bu_out_of_scope_raises_not_returns_empty() -> None:
    """🔴 越界必须显式报错，不得静默返回空结果。

    静默让用户以为"确实没有数据"，而真相是"你无权看" —— 这两件事在
    业务上完全不同，把后者伪装成前者会让人据此做出错误决策。
    """
    with pytest.raises(AuthError) as e:
        _ctx({"BU-A"}, {"*"}).narrow_bu(frozenset({"BU-B"}))
    assert e.value.code == "AUTH_BU_OUT_OF_SCOPE"


def test_narrow_region_wildcard_passes_through() -> None:
    ctx = _ctx({"BU-A"}, {"*"})
    assert ctx.narrow_region(frozenset({"华北"})) == frozenset({"华北"})
    assert ctx.narrow_region(None) is None


def test_narrow_region_out_of_scope_raises() -> None:
    with pytest.raises(AuthError) as e:
        _ctx({"BU-A"}, {"华东"}).narrow_region(frozenset({"华北"}))
    assert e.value.code == "AUTH_REGION_OUT_OF_SCOPE"


def test_context_is_immutable() -> None:
    """frozen=True 是刻意的：下游不得修改身份或权限范围。"""
    ctx = _ctx({"BU-A"}, {"*"})
    with pytest.raises((AttributeError, TypeError)):
        ctx.bu_codes = frozenset({"BU-A", "BU-B"})  # type: ignore[misc]


# ── conversation_id 必须由服务端签发（2026-09-09 决策）──────────
def test_client_invented_conversation_id_is_rejected(yr_token: str) -> None:
    """🔴 客户端自造的 conversation_id 一律拒绝。

    此前只把它当 HMAC 输入：冒充他人防住了（HMAC 压入已认证的 user_id），
    但客户端仍可自选取值。于是两段逻辑上无关的会话能复用同一取值，派生出
    同一个 session_id —— 在会话 A 里创建的 write_intent 会在会话 B 里被当成
    本会话的待确认项。这不是越权，是**串话**；而在二次确认这件事上，串话
    足以让用户确认了他没打算确认的那一单。
    """
    for bad in ["1", "c-1", "", "conv_short", "conv_" + "z" * 32, new_conversation_id().upper()]:
        with pytest.raises(AuthError) as exc:
            authenticate(yr_token, conversation_id=bad)
        assert exc.value.code == "AUTH_BAD_CONVERSATION_ID", f"未拒绝: {bad!r}"


def test_issued_conversation_ids_are_unique_and_well_shaped() -> None:
    """签发值必须唯一且带足够熵 —— 否则「服务端签发」只是换个地方产生碰撞。"""
    ids = [new_conversation_id() for _ in range(200)]
    assert len(set(ids)) == 200
    assert all(i.startswith("conv_") and len(i) == 37 for i in ids)


def test_issued_conversation_id_is_accepted(yr_token: str) -> None:
    """阳性对照：签发的取值必须能通过 —— 否则上一条用「全部拒绝」也能过。"""
    ctx = authenticate(yr_token, conversation_id=new_conversation_id())
    assert ctx.session_id
