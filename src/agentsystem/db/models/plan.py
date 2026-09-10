"""排产计划：production_plan（P2.1.5）。

S5 的联动分析（某订单延期会影响哪些下游）靠两件事成立：
关联到**订单行**的粒度，以及 ``stage_seq`` 表达的工序顺序。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Numeric,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from agentsystem.db.base import Base

PLAN_STATUSES = ("待排", "已排", "生产中", "已完成", "暂停")


class ProductionPlan(Base):
    """一条产线上的一道工序计划。"""

    __tablename__ = "production_plan"

    plan_no: Mapped[str] = mapped_column(String(32), primary_key=True)
    bu_code: Mapped[str] = mapped_column(String(8), nullable=False)
    line_code: Mapped[str] = mapped_column(String(32), nullable=False)
    product_code: Mapped[str] = mapped_column(String(32), nullable=False)
    color_code: Mapped[str] = mapped_column(String(32), nullable=False)
    process_stage: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="前处理/染色/后整理 或 压机/窑炉/抛光/分级"
    )

    # 工序顺序。延期沿 stage_seq 递增方向传播 —— 没有它，「染色晚了会不会影响
    # 后整理」只能靠 process_stage 的字符串去猜工艺顺序，而三个 BU 的工序名
    # 完全不同。
    stage_seq: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    planned_qty: Mapped[float] = mapped_column(Numeric(14, 3), nullable=False)
    qty_completed: Mapped[float] = mapped_column(
        Numeric(14, 3), nullable=False, server_default=text("0"), comment="实际完成量"
    )
    # 🔴 与 planned_qty 配套。缺它则 ETA 公式 planned_qty / capacity_per_hour
    #    隐含「计划量单位 == 产能单位」这个无据假设。
    uom: Mapped[str] = mapped_column(String(8), nullable=False)

    plan_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    plan_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 实绩。与 plan_* 的差即为延误量；为空表示尚未开工/完工。
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    changeover_min: Mapped[int] = mapped_column(
        nullable=False,
        server_default=text("0"),
        comment="换色/换规格调机时长（分钟），延误推理关键",
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    # 🔴 关联到**订单行**而非订单头：一张订单三个色号产生三条计划，
    #    以订单头为粒度时「这条计划服务哪一行」无法判断 —— 而这正是 S5 要答的。
    #    不设外键（§4.0），孤儿行由生成器断言与端点的 INNER JOIN 兜住。
    #    可空：备货型计划不对应任何订单行。
    related_order_line: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (
        CheckConstraint(
            "status IN ('待排', '已排', '生产中', '已完成', '暂停')",
            name="status_enum",
        ),
        CheckConstraint("plan_end >= plan_start", name="plan_window"),
        # 队列查询：某条线上按计划结束时间排队 —— ETA 推演要取「该线队尾」
        Index("ix_production_plan_line_queue", "line_code", "plan_end"),
        # S5 反查：这条订单行牵动哪些计划
        Index("ix_production_plan_order_line", "related_order_line"),
    )
