"""审计日志表。

宪法第一条的落地物：任何写操作都须留下操作者、内容、时间、变更前后值与
trace_id。本表**仅追加**，UPDATE / DELETE / TRUNCATE 由三层机制拦截
（P1.3.4，见 P1 详细设计 §3.4 的三层攻击实测）。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from agentsystem.db.base import Base


class AuditLog(Base):
    """审计日志。每次写操作产生 attempt 与 outcome 两行，以 trace_id 串联。

    两段式的原因：单事务写审计时业务回滚会让审计一并消失，而「被拒绝的写
    尝试」恰恰是审计最关心的事件；完全分离事务则有「业务已提交、审计未落」
    的窗口。两段式取中——崩溃留下的是可检测的悬挂 attempt，而非静默丢失。
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # ── 两段式的行区分。缺此字段则两行无法分辨，P1.3.1 与 P1.3.2 自相矛盾 ──
    phase: Mapped[str] = mapped_column(String(8), nullable=False, comment="attempt | outcome")

    # ── 宪法第一条要求的四要素 ──
    occurred_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    user_id: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="操作者，取自会话 context"
    )
    action_type: Mapped[str] = mapped_column(String(8), nullable=False, comment="read | write")
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # 入站 X-Trace-Id 的降级留存。客户端可指定权威 trace_id 即可让两次操作
    # 共用一个 id，污染 attempt/outcome 的串联，故只作参考不作权威（§2.5.3）。
    client_trace_id: Mapped[str | None] = mapped_column(String(64))

    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="langgraph | dify | rpa"
    )
    tool_name: Mapped[str | None] = mapped_column(String(64))

    # ── 变更前后值。仅 outcome 行承载 ──
    # attempt 行写在业务事务之外且之前，那时读到的前值随时可能被他人改掉，
    # 不可作为举证；前值必须在业务事务内、对目标行加锁后读。
    target_table: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    before_value: Mapped[dict | None] = mapped_column(JSONB)
    after_value: Mapped[dict | None] = mapped_column(JSONB)

    # ── 写操作的二次确认令牌（哈希，非明文）──
    # 存哈希而非原值：§10.3 的审计追溯视图会把本表渲染给用户，
    # 明文令牌泄露即等于确认能力泄露。
    confirm_token_sha256: Mapped[str | None] = mapped_column(String(64))

    status: Mapped[str | None] = mapped_column(
        String(16), comment="success | failed | degraded | cancelled"
    )
    error_code: Mapped[str | None] = mapped_column(String(48))

    # ── 评测归因埋点。这些字段必须在数据产生之前定，事后加是迁移 + 重跑 ──
    model_tier: Mapped[str | None] = mapped_column(String(8), comment="M0..M4")
    ttft_ms: Mapped[int | None] = mapped_column(Integer, comment="首 token 延迟，流式才有")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    retrieval_ms: Mapped[int | None] = mapped_column(Integer, comment="与 LLM 耗时分离以便归因")
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)

    request_payload: Mapped[dict | None] = mapped_column(JSONB, comment="已脱敏")
    response_payload: Mapped[dict | None] = mapped_column(JSONB)

    # ── RPA 取证引用（P4）。截图若不进证据链，就只是笔记本上的一堆文件 ──
    evidence_ref: Mapped[str | None] = mapped_column(String(256))

    __table_args__ = (
        # 按 trace_id 查是审计最主要的访问路径：把一次操作的 attempt/outcome 配对取出
        Index("ix_audit_log_trace_id", "trace_id"),
        # 合规举证最常见的形态：「把 SO-2026-000123 的全部审计记录调出来」
        Index("ix_audit_log_target", "target_table", "target_id"),
        Index("ix_audit_log_user_time", "user_id", "occurred_at"),
    )
