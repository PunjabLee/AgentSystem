"""write_intent 的数据访问层（P1 详细设计 §4）。

本模块**只做数据访问**，不写审计。审计由服务层的两段式装饰器负责
（P1.3.2/P1.3.3）——包括「用户拒绝」这一路：很多设计漏掉"被拒绝的确认"
也要留痕，那恰恰是审计上最该有的一条记录。

⚠️ 隔离级别：§4.1 的原子消费依赖 READ COMMITTED（PostgreSQL 默认）下的
    "阻塞于行锁 → 提交后重算谓词 → 0 行"行为。若调用方把事务提到
    REPEATABLE READ，同一场景会抛序列化错误而非返回 0 行，须自行捕获。
"""

import json
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agentsystem.errors import AuthError, ConfirmationRequired

#: 令牌 TTL。LangGraph 的 interrupt() 无内建超时（官方文档："waits
#: indefinitely"），TTL 必须自己实现，否则一个未回答的确认框可以永久有效。
DEFAULT_TTL = timedelta(minutes=10)

#: token_urlsafe(32) 产出 43 个字符，落在 String(64) 内。
#: 32 字节熵足以抵抗离线猜测，且令牌 TTL 只有 10 分钟。
_TOKEN_BYTES = 32


@dataclass(frozen=True, slots=True)
class MintedIntent:
    """铸造结果。

    ⚠️ ``confirm_token`` 绝不可进入模型上下文（§4.2 第一条）——须经 SSE
    独立事件通道或 REST 直接交付前端。若由模型输出，注入可诱导它在后续
    轮次把令牌当工具参数发出，而人类从未被询问过。
    """

    confirm_token: str
    intent_type: str
    expires_in_seconds: int


@dataclass(frozen=True, slots=True)
class ConsumedIntent:
    """消费结果。

    ``payload`` 一律取自库中，绝不取自确认请求（§4.2 第二条）——否则
    合法令牌配一份篡改载荷即可绕过：重放防住了，参数篡改没防。
    """

    confirm_token: str
    intent_type: str
    payload: dict
    user_id: str
    trace_id: str
    #: True 表示重复提交，本次未再执行，``result_ref`` 是首次执行的结果。
    replayed: bool = False
    result_ref: str | None = None


async def mint_intent(
    session: AsyncSession,
    *,
    session_id: str,
    user_id: str,
    trace_id: str,
    intent_type: str,
    payload: dict,
    ttl: timedelta = DEFAULT_TTL,
) -> MintedIntent:
    """铸造一枚待确认的写意图。

    Args:
        session: 业务会话。
        session_id: 取自 Gateway 会话，不取自请求体。
        user_id: 操作者。
        trace_id: 全链路追踪 id。
        intent_type: 如 ``create_order``。
        payload: 待用户确认的完整业务载荷。
        ttl: 令牌有效期。

    Returns:
        含明文令牌的铸造结果。
    """
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    await session.execute(
        text(
            "INSERT INTO write_intent "
            "(confirm_token, session_id, user_id, trace_id, intent_type, payload, "
            " state, expires_at) "
            "VALUES (:tok, :sid, :uid, :tid, :itype, CAST(:payload AS jsonb), "
            " 'pending', now() + make_interval(secs => :ttl_seconds))"
        ),
        {
            "tok": token,
            "sid": session_id,
            "uid": user_id,
            "tid": trace_id,
            "itype": intent_type,
            "payload": json.dumps(payload),
            # asyncpg 按类型严格绑定，字符串无法转 interval；
            # make_interval(secs => …) 接受 double precision。
            "ttl_seconds": ttl.total_seconds(),
        },
    )
    return MintedIntent(
        confirm_token=token,
        intent_type=intent_type,
        expires_in_seconds=int(ttl.total_seconds()),
    )


async def consume_intent(
    session: AsyncSession, *, confirm_token: str, session_id: str
) -> ConsumedIntent:
    """原子消费一枚令牌（§4.1）。

    单条 UPDATE 完成「校验 + 状态转移」，不用「先查后写」——后者在并发下
    必然有窗口，三位评审专家独立指出过这一点。READ COMMITTED 下第二个
    UPDATE 阻塞于行锁，第一个提交后重算谓词，``state`` 已变则返回 0 行。

    Args:
        session: 业务会话。
        confirm_token: 用户提交的令牌。
        session_id: **取自 Gateway 会话**，不取自请求体。缺这一项则任何
            持令牌的会话都能完成确认——令牌本身不可伪造不可重放，漏的是
            「谁在确认」。

    Returns:
        消费结果；重复提交时 ``replayed`` 为 True。

    Raises:
        AuthError: 令牌无效、已被消费或不属于本会话。
        ConfirmationRequired: 令牌已过期，需重新确认。
    """
    row = (
        await session.execute(
            text(
                "UPDATE write_intent "
                "   SET state = 'confirmed', consumed_at = now() "
                " WHERE confirm_token = :tok "
                "   AND session_id    = :sid "
                "   AND state         = 'pending' "
                "   AND expires_at    > now() "
                "RETURNING intent_type, payload, user_id, trace_id"
            ),
            {"tok": confirm_token, "sid": session_id},
        )
    ).one_or_none()

    if row is not None:
        return ConsumedIntent(
            confirm_token=confirm_token,
            intent_type=row.intent_type,
            payload=row.payload,
            user_id=row.user_id,
            trace_id=row.trace_id,
        )

    return await _explain_consume_failure(session, confirm_token, session_id)


async def _explain_consume_failure(
    session: AsyncSession, confirm_token: str, session_id: str
) -> ConsumedIntent:
    """0 行时判定原因。要么返回重放结果，要么抛出对应异常。

    诊断查询**同样按 session_id 过滤**。这不是复制粘贴——若不过滤，
    攻击者可用一枚偷来的令牌区分「不存在」与「属于他人」，把本函数变成
    令牌存在性的预言机。过滤后两种情形都落到同一句「令牌无效」。
    """
    row = (
        await session.execute(
            text(
                "SELECT state, result_ref, intent_type, payload, user_id, trace_id, "
                "       expires_at <= now() AS is_expired "
                "  FROM write_intent "
                " WHERE confirm_token = :tok AND session_id = :sid"
            ),
            {"tok": confirm_token, "sid": session_id},
        )
    ).one_or_none()

    if row is None:
        # 不存在、或属于别的会话——刻意不区分（见上）。
        raise AuthError("确认令牌无效", code="AUTH_CONFIRM_TOKEN_INVALID")

    if row.state == "pending" and row.is_expired:
        raise ConfirmationRequired("确认已超时，请重新发起并确认", code="CONF_TOKEN_EXPIRED")

    if row.state == "confirmed" and row.result_ref is not None:
        # 至多一次语义（§4.2 第三条）：重复提交凭 result_ref 返回原结果，
        # 不重新执行。
        return ConsumedIntent(
            confirm_token=confirm_token,
            intent_type=row.intent_type,
            payload=row.payload,
            user_id=row.user_id,
            trace_id=row.trace_id,
            replayed=True,
            result_ref=row.result_ref,
        )

    if row.state == "confirmed":
        # 已消费但无 result_ref：上次执行未落结果即失败。令牌已作废，
        # **不重试**——重试等于把一次失败的写操作变成可能成功的第二次，
        # 而人类只确认过一次。
        raise AuthError("该确认已被使用且执行未完成，请重新发起", code="AUTH_CONFIRM_TOKEN_SPENT")

    # cancelled / expired 等终态，均不可逆。
    raise AuthError(f"确认令牌状态为 {row.state}，不可使用", code="AUTH_CONFIRM_TOKEN_INVALID")


async def cancel_intent(session: AsyncSession, *, confirm_token: str, session_id: str) -> bool:
    """用户拒绝：pending → cancelled。

    终态不可逆（§4 第四条）。用户拒绝后若想改单，是「作废旧令牌 + 铸造
    新令牌」，不是把 cancelled 改回 pending。

    Returns:
        True 表示确实由 pending 转为 cancelled；False 表示令牌不在
        pending 态（已消费/已过期/不属于本会话），调用方据此决定是否
        视为幂等成功。
    """
    result = await session.execute(
        text(
            "UPDATE write_intent "
            "   SET state = 'cancelled', consumed_at = now() "
            " WHERE confirm_token = :tok AND session_id = :sid AND state = 'pending'"
        ),
        {"tok": confirm_token, "sid": session_id},
    )
    return result.rowcount == 1
