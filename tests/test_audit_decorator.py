"""审计装饰器与两段式写入的行为断言（P1 详细设计 §3）。

审计行不可删（P1.3.4），故每次运行都会永久追加若干行 —— 这是 append-only
的正确行为。用例按各自的 trace_id（uuid7，每次新生成）检索，互不干扰。
"""

import uuid

import pytest
from sqlalchemy import text

from agentsystem.audit import WriteOutcome, audited
from agentsystem.db.session import app_session, get_app_sessionmaker
from agentsystem.errors import BusinessRejection, ConfirmationRequired
from agentsystem.gateway.context import RequestContext, reset_context, set_context


@pytest.fixture
def ctx() -> RequestContext:
    """装上一个身份上下文，并在用例结束后还原，避免跨用例泄漏。"""
    c = RequestContext(
        user_id="u_tile_01",
        session_id="sess-audit-test",
        trace_id=str(uuid.uuid7()),
        bu_codes=frozenset({"BU-B"}),
        regions=frozenset({"华东"}),
        client_trace_id="client-supplied-id",
    )
    token = set_context(c)
    yield c
    reset_context(token)


async def _rows(trace_id: str) -> list:
    """按 trace_id 取回审计行，按 id 升序（即写入顺序）。"""
    async with get_app_sessionmaker()() as s:
        return (
            await s.execute(
                text(
                    "SELECT phase, status, error_code, action_type, tool_name, "
                    "       target_id, before_value, after_value, "
                    "       confirm_token_sha256, client_trace_id, latency_ms "
                    "  FROM audit_log WHERE trace_id = :t ORDER BY id"
                ),
                {"t": trace_id},
            )
        ).all()


# ── 宪法第一条的机械保障 ──────────────────────────────────────
async def test_missing_confirm_token_is_refused(ctx: RequestContext) -> None:
    """🔴 缺令牌直接拒，且**一行审计都不写**。

    一次连令牌都没带的调用不是「尝试写入」，是调用方用错了 API；
    给它记 attempt 会让审计里混进大量非事件。
    """

    @audited("write", target_table="sales_order", require_confirm=True)
    async def create_order(*, confirm_token: str | None = None) -> str:
        raise AssertionError("不应被执行")

    with pytest.raises(ConfirmationRequired) as e:
        await create_order()
    assert e.value.code == "CONF_TOKEN_MISSING"
    assert e.value.http_status == 428
    assert await _rows(ctx.trace_id) == []


def test_require_confirm_on_read_is_a_programming_error() -> None:
    """读操作不需要确认，这样写必是笔误 —— 在装饰时就炸，别等上线。"""
    with pytest.raises(ValueError, match="只对 action_type='write'"):

        @audited("read", require_confirm=True)
        async def query() -> None: ...


# ── 两段式 ────────────────────────────────────────────────────
async def test_write_emits_attempt_then_outcome(ctx: RequestContext) -> None:
    """成功的写：两行，同 trace_id，attempt 在前。"""

    @audited("write", target_table="sales_order", require_confirm=True)
    async def create_order(*, confirm_token: str | None = None) -> WriteOutcome:
        return WriteOutcome(
            value="SO-2026-000123",
            target_id="SO-2026-000123",
            before_value=None,
            after_value={"status": "已确认"},
        )

    assert await create_order(confirm_token="tok-abc") == "SO-2026-000123"

    rows = await _rows(ctx.trace_id)
    assert len(rows) == 2
    attempt, outcome = rows

    assert attempt.phase == "attempt"
    assert attempt.status is None, "attempt 行此刻无从判定结果"
    assert attempt.before_value is None, "前值只能在业务事务内加锁后读"

    assert outcome.phase == "outcome"
    assert outcome.status == "success"
    assert outcome.target_id == "SO-2026-000123"
    assert outcome.after_value == {"status": "已确认"}
    assert outcome.latency_ms is not None


async def test_audit_survives_business_rollback(ctx: RequestContext) -> None:
    """🔴 两段式存在的全部理由：业务回滚了，审计两行都还在。

    单事务写审计的话，这里会一行不剩 —— 而「被拒绝的写尝试」恰恰是
    审计最关心的事件。
    """
    probe = f"rollback-probe-{ctx.trace_id[:8]}"

    @audited("write", target_table="write_intent", require_confirm=True)
    async def failing_write(*, confirm_token: str | None = None) -> None:
        async with app_session() as s:
            await s.execute(
                text(
                    "INSERT INTO write_intent (confirm_token, session_id, user_id, "
                    " trace_id, intent_type, payload, state, expires_at) "
                    "VALUES (:t, 's', 'u', :tid, 'probe', '{}'::jsonb, 'pending', "
                    " now() + interval '1 hour')"
                ),
                {"t": probe, "tid": ctx.trace_id},
            )
            raise BusinessRejection("库存不足", code="BIZ_INSUFFICIENT_STOCK")

    with pytest.raises(BusinessRejection):
        await failing_write(confirm_token="tok-xyz")

    rows = await _rows(ctx.trace_id)
    assert [r.phase for r in rows] == ["attempt", "outcome"]
    assert rows[1].status == "failed"
    assert rows[1].error_code == "BIZ_INSUFFICIENT_STOCK"

    # 业务写入确实回滚了 —— 证明审计不是搭了业务事务的便车
    async with get_app_sessionmaker()() as s:
        left = (
            await s.execute(
                text("SELECT count(*) FROM write_intent WHERE confirm_token = :t"),
                {"t": probe},
            )
        ).scalar()
    assert left == 0, "业务应已回滚"


async def test_read_emits_single_row(ctx: RequestContext) -> None:
    """读只出一行：读失败没有副作用，多一次独立提交是白付延迟。"""

    @audited("read", target_table="inventory_batch", tool_name="query_inventory")
    async def query() -> list[str]:
        return ["B2409"]

    assert await query() == ["B2409"]
    rows = await _rows(ctx.trace_id)
    assert len(rows) == 1
    assert rows[0].phase == "outcome"
    assert rows[0].action_type == "read"
    assert rows[0].tool_name == "query_inventory"


# ── 令牌与上下文的落库形态 ────────────────────────────────────
async def test_confirm_token_is_stored_hashed_not_plaintext(ctx: RequestContext) -> None:
    """🔴 审计表存令牌哈希，绝不存明文。

    审计表按设计对 app_ro 可读、永不删除、且会被导出做合规举证 ——
    它是最不该存放可重用凭据的地方。
    """
    import hashlib

    plaintext = "super-secret-confirm-token"

    @audited("write", target_table="sales_order", require_confirm=True)
    async def w(*, confirm_token: str | None = None) -> None:
        return None

    await w(confirm_token=plaintext)
    rows = await _rows(ctx.trace_id)
    expected = hashlib.sha256(plaintext.encode()).hexdigest()
    for r in rows:
        assert r.confirm_token_sha256 == expected
        assert plaintext not in (r.confirm_token_sha256 or "")


async def test_client_trace_id_recorded_but_not_authoritative(ctx: RequestContext) -> None:
    """入站 X-Trace-Id 落在 client_trace_id，权威 trace_id 仍是服务端生成的。"""

    @audited("read")
    async def q() -> int:
        return 1

    await q()
    rows = await _rows(ctx.trace_id)
    assert rows[0].client_trace_id == "client-supplied-id"
    assert ctx.trace_id != "client-supplied-id"


async def test_unexpected_exception_is_recorded_as_failed(ctx: RequestContext) -> None:
    """非 AppError 的异常也必须留痕，否则崩溃路径在审计上是盲区。"""

    @audited("read")
    async def boom() -> None:
        raise RuntimeError("unexpected")

    with pytest.raises(RuntimeError):
        await boom()
    rows = await _rows(ctx.trace_id)
    assert rows[0].status == "failed"
    assert rows[0].error_code == "RuntimeError"


async def test_metrics_records_actual_tier_not_requested(ctx: RequestContext) -> None:
    """🔴 model_tier 必须记**实际使用的档**，不是请求的档。

    发生降级时两者不同。若记请求档，P3 的「M3 vs M4」对比会把一批实际由
    M3 产生的数据算到 M4 头上 —— 结论作废，且无从事后察觉。
    """
    from agentsystem.audit import metrics_from_llm_result
    from agentsystem.audit.writer import write_outcome
    from agentsystem.llm.types import LLMResult

    result = LLMResult(
        content="ok",
        finish_reason="stop",
        tier_used="M3",
        degraded_from="M4",  # 请求的是 M4，实际落到 M3
        latency_ms=1200,
        ttft_ms=340,
        prompt_tokens=396,
        completion_tokens=96,
    )
    metrics = metrics_from_llm_result(result)
    assert metrics["model_tier"] == "M3", "记的应是实际档"

    await write_outcome(ctx, action_type="read", source="langgraph", status="success", **metrics)
    rows = await _rows(ctx.trace_id)
    assert len(rows) == 1


async def test_llm_metrics_land_in_audit_row(ctx: RequestContext) -> None:
    """P1.2.4：埋点必须真落进审计表，采到了落不了库等于没采。"""
    from agentsystem.audit.writer import write_outcome

    await write_outcome(
        ctx,
        action_type="read",
        source="langgraph",
        status="success",
        model_tier="M4",
        ttft_ms=331,
        prompt_tokens=396,
        completion_tokens=96,
        latency_ms=768,
    )
    async with get_app_sessionmaker()() as s:
        row = (
            await s.execute(
                text(
                    "SELECT model_tier, ttft_ms, prompt_tokens, completion_tokens, latency_ms "
                    "FROM audit_log WHERE trace_id = :t"
                ),
                {"t": ctx.trace_id},
            )
        ).one()
    assert (row.model_tier, row.ttft_ms, row.prompt_tokens, row.completion_tokens) == (
        "M4",
        331,
        396,
        96,
    )
