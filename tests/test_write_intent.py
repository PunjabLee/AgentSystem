"""write_intent 原子消费的行为断言（P1 详细设计 §4）。

重点不在"能跑通"，而在四条负路径：换会话、重复消费、过期、并发双花。
其中并发双花是本表存在的全部理由——若"先查后写"就够，这张表可以不建。
"""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agentsystem.db.session import get_app_sessionmaker
from agentsystem.errors import AuthError, ConfirmationRequired
from agentsystem.intent import cancel_intent, consume_intent, mint_intent

SID = "sess-under-test"
PAYLOAD = {"order_no": "SO-2026-0001", "qty": 1200, "bu_code": "BU-B"}


async def _mint(db: AsyncSession, *, session_id: str = SID, ttl: timedelta | None = None) -> str:
    minted = await mint_intent(
        db,
        session_id=session_id,
        user_id="u-tester",
        trace_id="trace-intent-test",
        intent_type="create_order",
        payload=PAYLOAD,
        ttl=ttl if ttl is not None else timedelta(minutes=10),
    )
    return minted.confirm_token


async def test_consume_happy_path(db: AsyncSession) -> None:
    """正常路径：载荷原样取自库中，不取自请求。"""
    token = await _mint(db)
    consumed = await consume_intent(db, confirm_token=token, session_id=SID)
    assert consumed.payload == PAYLOAD
    assert consumed.intent_type == "create_order"
    assert consumed.replayed is False


async def test_wrong_session_is_rejected_and_token_survives(db: AsyncSession) -> None:
    """换个会话拿同一枚令牌 —— 必须被拒，且**不能烧掉令牌**。

    若失败路径顺手把令牌置为终态，攻击者就能用一枚偷来的令牌
    单方面作废他人的待确认操作（拒绝服务）。
    """
    token = await _mint(db)
    with pytest.raises(AuthError) as e:
        await consume_intent(db, confirm_token=token, session_id="sess-attacker")
    assert e.value.code == "AUTH_CONFIRM_TOKEN_INVALID"
    assert e.value.retryable is False

    state = (
        await db.execute(
            text("SELECT state FROM write_intent WHERE confirm_token = :t"), {"t": token}
        )
    ).scalar()
    assert state == "pending", "失败的越权尝试不得改变令牌状态"

    # 正主仍可正常消费
    assert (await consume_intent(db, confirm_token=token, session_id=SID)).payload == PAYLOAD


async def test_second_consume_is_rejected(db: AsyncSession) -> None:
    """至多一次：第二次消费失败，且不因"上次没落结果"而放行重试。"""
    token = await _mint(db)
    await consume_intent(db, confirm_token=token, session_id=SID)
    with pytest.raises(AuthError) as e:
        await consume_intent(db, confirm_token=token, session_id=SID)
    assert e.value.code == "AUTH_CONFIRM_TOKEN_SPENT"


async def test_replay_returns_original_result(db: AsyncSession) -> None:
    """重复提交凭 result_ref 返回原结果，不重新执行（§4.2 第三条）。"""
    token = await _mint(db)
    await consume_intent(db, confirm_token=token, session_id=SID)
    await db.execute(
        text("UPDATE write_intent SET result_ref = :r WHERE confirm_token = :t"),
        {"r": "SO-2026-0001", "t": token},
    )
    again = await consume_intent(db, confirm_token=token, session_id=SID)
    assert again.replayed is True
    assert again.result_ref == "SO-2026-0001"


async def test_expired_token(db: AsyncSession) -> None:
    """过期是惰性判定，不依赖任何后台清理任务。"""
    token = await _mint(db, ttl=timedelta(seconds=-1))
    with pytest.raises(ConfirmationRequired) as e:
        await consume_intent(db, confirm_token=token, session_id=SID)
    assert e.value.code == "CONF_TOKEN_EXPIRED"
    assert e.value.http_status == 428


async def test_unknown_token(db: AsyncSession) -> None:
    """不存在的令牌与他人的令牌返回同一个 code，不做存在性预言机。"""
    with pytest.raises(AuthError) as e:
        await consume_intent(db, confirm_token="no-such-token", session_id=SID)
    assert e.value.code == "AUTH_CONFIRM_TOKEN_INVALID"


async def test_cancel_then_consume(db: AsyncSession) -> None:
    """用户拒绝后终态不可逆。"""
    token = await _mint(db)
    assert await cancel_intent(db, confirm_token=token, session_id=SID) is True
    assert await cancel_intent(db, confirm_token=token, session_id=SID) is False
    with pytest.raises(AuthError):
        await consume_intent(db, confirm_token=token, session_id=SID)


async def test_concurrent_consume_exactly_one_wins() -> None:
    """并发双花：两个连接同时消费同一枚令牌，必须恰好一个成功。

    这是本表存在的理由。用独立连接而非同一会话 —— 同会话内串行执行，
    根本不会产生竞争，那样的"并发测试"是自欺。

    READ COMMITTED 下第二个 UPDATE 阻塞于行锁，第一个提交后重算谓词，
    state 已变则匹配 0 行。
    """
    factory = get_app_sessionmaker()
    async with factory() as setup:
        async with setup.begin():
            token = await _mint(setup)

    # 屏障确保两个事务都已开启、且都停在 UPDATE 之前 —— 没有它，
    # asyncio.gather 完全可能让二者先后执行完，测到的就不是行锁仲裁，
    # 而是"顺序执行两次消费"，那已由 test_second_consume_is_rejected 覆盖。
    barrier = asyncio.Barrier(2)

    async def attempt() -> str:
        async with factory() as s:
            async with s.begin():
                # 先发一句无害语句，逼迫连接真正开启事务
                await s.execute(text("SELECT 1"))
                await barrier.wait()
                try:
                    await consume_intent(s, confirm_token=token, session_id=SID)
                except AuthError as exc:
                    return exc.code
                return "OK"

    try:
        outcomes = await asyncio.gather(attempt(), attempt())
        assert sorted(outcomes) == ["AUTH_CONFIRM_TOKEN_SPENT", "OK"], (
            f"恰好一个应成功，实际: {outcomes}"
        )
    finally:
        async with factory() as cleanup:
            async with cleanup.begin():
                await cleanup.execute(
                    text("DELETE FROM write_intent WHERE confirm_token = :t"), {"t": token}
                )
