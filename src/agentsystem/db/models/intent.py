"""写意图状态表。

宪法第一条「写操作必须二次确认」的落地物。三位评审专家独立指出：
`confirm_token` 的「一次性」若只是文字承诺——无落库位置、无唯一约束、
无 TTL、用「先查后写」消费——在并发下必然有窗口。本表提供落点。
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from agentsystem.db.base import Base

#: 状态机的四个终态之一为 pending，其余三个不可逆。
#: 用户拒绝后想改单，是「作废旧令牌 + 铸造新令牌」，不是把 cancelled 改回 pending。
INTENT_STATES = ("pending", "confirmed", "cancelled", "expired")


class WriteIntent(Base):
    """写意图。用户确认过的载荷在此暂存，执行时只从这里取。

    三条不可省的约束（P1 详细设计 §4.2）：

    1. 令牌绝不进入模型上下文——否则注入可诱导模型在后续轮次把它当作
       工具参数发出，人类从未被询问。
    2. 执行只取本表的 payload，确认请求不得携带业务参数——否则合法令牌
       配一份篡改载荷即可绕过：重放防住了，参数篡改没防。
    3. 消费语义为「至多一次」：执行失败则令牌作废，不重试。
    """

    __tablename__ = "write_intent"

    # 服务端 secrets.token_urlsafe(32) 铸造。作主键即天然唯一，
    # 原子消费靠「UPDATE ... WHERE state='pending'」的行锁仲裁，不用先查后写。
    confirm_token: Mapped[str] = mapped_column(String(64), primary_key=True)

    # 🔴 消费时必须比对。缺它则任何持令牌的会话都能完成确认——
    # 令牌本身不可伪造不可重放，漏的是「谁在确认」（违反宪法一、十）。
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)

    intent_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="create_order | update_order_status | ..."
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, comment="用户确认过的完整载荷")

    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))

    # LangGraph 的 interrupt() 无内建超时（官方："waits indefinitely"），
    # TTL 必须自己实现。过期为惰性判定——消费时 expires_at > now() 不满足
    # 即视为过期，不需要后台清理任务；定期清理仅为控制表膨胀。
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    result_ref: Mapped[str | None] = mapped_column(
        String(64), comment="执行结果的业务主键，支撑重复提交返回原结果"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'confirmed', 'cancelled', 'expired')",
            name="state_enum",
        ),
        Index("ix_write_intent_session_state", "session_id", "state"),
        # 部分索引：只有 pending 行需要按过期时间扫描，终态行永不参与
        Index(
            "ix_write_intent_expires_pending",
            "expires_at",
            postgresql_where=text("state = 'pending'"),
        ),
    )
