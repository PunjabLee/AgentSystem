"""两段式审计写入（P1 详细设计 §3.1–3.2）。

**为什么是两段式**：单事务写审计，业务失败回滚时审计一并消失 —— 而
「被拒绝的写尝试」恰恰是审计最关心的事件。完全分离事务，则有「业务已提交、
审计未落」的窗口。两段式取中：attempt 行独立提交在前，outcome 行独立提交
在后，同 trace_id 串联。崩溃留下的是**可检测的悬挂 attempt**，而非静默丢失。

**两条不可动的约束**：

1. 审计走独立池，不得与业务共池。业务事务已持一条连接，再从同池 acquire
   第二条 —— 池大小 N 时 N 个并发全部卡死在 acquire。这是死锁而非变慢。
2. ``before_value`` 只落 outcome 行。前值必须在业务事务内、对目标行加锁后
   读；attempt 行写在事务之外且之前，那时读到的值随时可能被他人改掉，
   不可作为举证。
"""

import hashlib
import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from agentsystem.db.session import get_audit_sessionmaker
from agentsystem.gateway.context import RequestContext

if TYPE_CHECKING:  # 仅类型标注需要，运行时不建立 audit → llm 的依赖
    from agentsystem.llm.types import LLMResult

_INSERT = text(
    "INSERT INTO audit_log ("
    "  phase, user_id, action_type, trace_id, client_trace_id, session_id, source,"
    "  tool_name, target_table, target_id, before_value, after_value,"
    "  confirm_token_sha256, status, error_code, latency_ms, request_payload,"
    "  model_tier, ttft_ms, retrieval_ms, prompt_tokens, completion_tokens"
    ") VALUES ("
    "  :phase, :user_id, :action_type, :trace_id, :client_trace_id, :session_id, :source,"
    "  :tool_name, :target_table, :target_id,"
    "  CAST(:before_value AS jsonb), CAST(:after_value AS jsonb),"
    "  :confirm_token_sha256, :status, :error_code, :latency_ms,"
    "  CAST(:request_payload AS jsonb),"
    "  :model_tier, :ttft_ms, :retrieval_ms, :prompt_tokens, :completion_tokens"
    ")"
)


def _json(value: Any) -> str | None:
    """序列化为 JSONB 入参；None 原样透传，让列保持 SQL NULL。"""
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


def hash_confirm_token(token: str | None) -> str | None:
    """令牌只存哈希。

    审计表是**最不该**存放可重用凭据的地方：它按设计对 app_ro 可读、
    永不删除、且会被导出做合规举证。存哈希后仍可回答「这条记录对应哪枚
    令牌」，但拿到审计快照的人无法replay 任何一次写操作。
    """
    return None if token is None else hashlib.sha256(token.encode("utf-8")).hexdigest()


#: 计量类字段的缺省值。attempt 行与不涉及模型的操作都用它填空 ——
#: 少传一个参数 psycopg 会直接报缺参数，集中给默认比每处手写可靠。
_METRIC_DEFAULTS: dict[str, Any] = {
    "model_tier": None,
    "ttft_ms": None,
    "retrieval_ms": None,
    "prompt_tokens": None,
    "completion_tokens": None,
}


async def _write(**params: Any) -> None:
    """向独立池提交一行审计。"""
    async with get_audit_sessionmaker()() as session, session.begin():
        await session.execute(_INSERT, {**_METRIC_DEFAULTS, **params})


async def write_attempt(
    ctx: RequestContext,
    *,
    action_type: str,
    source: str,
    tool_name: str | None = None,
    target_table: str | None = None,
    confirm_token: str | None = None,
    request_payload: dict | None = None,
) -> None:
    """写 attempt 行 —— 业务事务之外、之前，独立提交。

    ``status`` 与 ``before_value`` 一律留空：此刻业务尚未执行，前者无从判定，
    后者读到的值随时可能被他人改掉。
    """
    await _write(
        phase="attempt",
        user_id=ctx.user_id,
        action_type=action_type,
        trace_id=ctx.trace_id,
        client_trace_id=ctx.client_trace_id,
        session_id=ctx.session_id,
        source=source,
        tool_name=tool_name,
        target_table=target_table,
        target_id=None,
        before_value=None,
        after_value=None,
        confirm_token_sha256=hash_confirm_token(confirm_token),
        status=None,
        error_code=None,
        latency_ms=None,
        request_payload=_json(request_payload),
    )


async def write_outcome(
    ctx: RequestContext,
    *,
    action_type: str,
    source: str,
    status: str,
    tool_name: str | None = None,
    target_table: str | None = None,
    target_id: str | None = None,
    before_value: dict | None = None,
    after_value: dict | None = None,
    confirm_token: str | None = None,
    error_code: str | None = None,
    latency_ms: int | None = None,
    model_tier: str | None = None,
    ttft_ms: int | None = None,
    retrieval_ms: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> None:
    """写 outcome 行 —— 业务事务之后，独立提交。

    ``status`` 取 ``success`` | ``failed`` | ``cancelled``。``cancelled``
    覆盖「用户拒绝确认」—— 很多设计漏掉被拒绝的确认，而那恰恰是审计上
    最该留痕的一类事件：它记录的是一次「差点发生」的写操作。
    """
    await _write(
        phase="outcome",
        user_id=ctx.user_id,
        action_type=action_type,
        trace_id=ctx.trace_id,
        client_trace_id=ctx.client_trace_id,
        session_id=ctx.session_id,
        source=source,
        tool_name=tool_name,
        target_table=target_table,
        target_id=target_id,
        before_value=_json(before_value),
        after_value=_json(after_value),
        confirm_token_sha256=hash_confirm_token(confirm_token),
        status=status,
        error_code=error_code,
        latency_ms=latency_ms,
        request_payload=None,
        # ── P1.2.4 的埋点落点。model_tier 记**实际使用的档**，降级后与
        # 请求档不同 —— P3 的对比结论按这一列归因，记错档等于结论作废。
        model_tier=model_tier,
        ttft_ms=ttft_ms,
        retrieval_ms=retrieval_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def metrics_from_llm_result(result: LLMResult) -> dict[str, Any]:
    """把一次模型调用的计量转成 ``write_outcome`` 的关键字参数。

    存在的理由是防一类具体的错误：``model_tier`` 必须记 **实际使用的档**
    （``tier_used``）而非请求的档。发生降级时两者不同，若记请求档，P3 的
    「M3 vs M4」对比会把一批实际由 M3 产生的数据算到 M4 头上 —— 结论作废，
    且无从事后察觉。集中在这里映射一次，比每个调用点各写一遍可靠。

    Args:
        result: 网关返回的调用结果。

    Returns:
        可直接 ``**`` 展开进 ``write_outcome`` 的字典。
    """
    return {
        "model_tier": result.tier_used,
        "ttft_ms": result.ttft_ms,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "latency_ms": result.latency_ms,
    }
