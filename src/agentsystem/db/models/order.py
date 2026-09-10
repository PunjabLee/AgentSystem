"""订单：sales_order / sales_order_line（P2.1.3 · P2.1.7）。

``sales_order.confirm_token`` 是整个写路径的幂等键，也是「含 ``interrupt()``
的节点会从头重放」这一框架语义的最后一道防线（宪法一之附则）。
**移除该唯一约束等同于移除二次确认的兜底。**
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from agentsystem.db.base import Base

#: 拼批策略（P2.1.7）。原设计是 ``same_batch_req BOOLEAN`` —— 只能表达
#: 「必须同缸」与「随便」，而真实业务里最常见的恰恰是中间那档：
#: 可以跨缸，但缸间色差要在容差内。布尔值把这一档挤没了，导致 S2 的跨缸
#: 澄清场景无数据可演。
BATCH_POLICIES = (
    "SAME_BATCH",  # 必须同缸同批
    "CROSS_OK_WITHIN_TOL",  # 可跨缸，但缸间 ΔE 差值须 ≤ delta_e_tolerance
    "ANY",  # 不限
)

ORDER_STATUSES = ("待评审", "已确认", "生产中", "部分发货", "已完成", "已取消")


class SalesOrder(Base):
    """订单头。"""

    __tablename__ = "sales_order"

    order_no: Mapped[str] = mapped_column(String(32), primary_key=True, comment="SO-2026-000123")
    bu_code: Mapped[str] = mapped_column(String(8), nullable=False)

    # 🔴 区域级权限的落点（P2.4.2）。users.yaml 给每个用户配了 regions，
    #    RequestContext.narrow_region() 也早就在了，但此前**业务表里没有任何
    #    region 列** —— 区域级越权根本无从表达，权限模型缺了一半。
    #    与 bu_code 同样反范式化到本表：不建外键后 JOIN 不再有约束保障，
    #    把权限过滤架在 JOIN 上等于把防线建在数据库不再守护的关系上。
    region: Mapped[str] = mapped_column(String(16), nullable=False, comment="客户所属区域")
    customer_code: Mapped[str] = mapped_column(String(32), nullable=False, comment="化名客户编码")
    customer_name: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="化名，如「华东建材-A001」"
    )
    order_type: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="经销/工程/出口/样品"
    )
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    required_date: Mapped[date] = mapped_column(Date, nullable=False, comment="客户要求交期")
    promised_date: Mapped[date | None] = mapped_column(Date, comment="承诺交期")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    total_amount: Mapped[float | None] = mapped_column(Numeric(14, 2))
    policy_code: Mapped[str | None] = mapped_column(String(32), comment="适用营销政策")
    created_by: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    # 🔴 幂等键。写端点靠 ON CONFLICT (confirm_token) DO NOTHING 仲裁重复提交。
    #    含 interrupt() 的节点在 resume 时**从节点开头重放**（实测，见
    #    tests/test_langgraph_semantics.py），本约束是拓扑被改坏时的最后防线。
    #    可空：不经二次确认创建的历史数据（如种子数据）没有令牌。
    confirm_token: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        # 唯一约束单独声明而非写在列上：命名后 CI 才能机械断言它存在。
        UniqueConstraint("confirm_token", name="uq_sales_order_confirm_token"),
        CheckConstraint(
            "status IN ('待评审', '已确认', '生产中', '部分发货', '已完成', '已取消')",
            name="status_enum",
        ),
        Index("ix_sales_order_bu_status", "bu_code", "status"),
        # 权限过滤每次查询都走，bu+region 是复合上界
        Index("ix_sales_order_scope", "bu_code", "region"),
        # 支撑「哪些订单快到期了」——S4/S5 的高频入口
        Index("ix_sales_order_required_date", "required_date"),
    )


class SalesOrderLine(Base):
    """订单行。

    粒度说明：``production_plan`` 关联到**订单行**而非订单头 —— 一张订单三个
    色号会产生三条产线计划，以订单头为粒度时「这条计划服务哪一行」无法判断，
    而 S5 的联动分析正是要回答这个。
    """

    __tablename__ = "sales_order_line"

    line_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # 不设外键（§4.0）。写端点插入前须校验 order_no 存在 —— 那是 §4.0 决策
    # 成立的前提之一，不是可选项。
    order_no: Mapped[str] = mapped_column(String(32), nullable=False)

    # 行号对用户可读，line_id 只是代理键。两者都要：用户说「第 2 行改一下」，
    # 而 line_id 是全局自增的，对用户毫无意义。
    line_no: Mapped[int] = mapped_column(nullable=False, comment="行号，订单内从 1 递增")

    product_code: Mapped[str] = mapped_column(String(32), nullable=False)
    spec: Mapped[str] = mapped_column(String(64), nullable=False, comment="门幅×克重 / 边长×厚度")
    color_code: Mapped[str] = mapped_column(String(32), nullable=False)
    grade_required: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="优等品/一等品/合格品"
    )

    # P2.1.7：由 same_batch_req BOOLEAN 改来。见 BATCH_POLICIES 的说明。
    batch_policy: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'ANY'")
    )
    # 仅当 batch_policy = CROSS_OK_WITHIN_TOL 时有意义：缸间 ΔE 差值的上限。
    # ⚠️ 比的是**两批各自 ΔE 的差值**，不是拿两批的 delta_e 直接相减 ——
    #    inventory_batch.delta_e 的基准是「本批 vs 标准大样」（QC-STD-006 §2.3）。
    delta_e_tolerance: Mapped[float | None] = mapped_column(Numeric(5, 2))

    qty: Mapped[float] = mapped_column(Numeric(14, 3), nullable=False)
    uom: Mapped[str] = mapped_column(String(8), nullable=False, comment="米 / 平方米 / 件")
    unit_price: Mapped[float | None] = mapped_column(Numeric(12, 4))
    line_status: Mapped[str | None] = mapped_column(String(16))

    __table_args__ = (
        UniqueConstraint("order_no", "line_no", name="uq_sales_order_line_no"),
        CheckConstraint(
            "batch_policy IN ('SAME_BATCH', 'CROSS_OK_WITHIN_TOL', 'ANY')",
            name="batch_policy_enum",
        ),
        # 容差只在跨缸策略下有意义。写成约束而非靠调用方自觉 ——
        # 一个 SAME_BATCH 配着容差值的行，读的人无从判断哪个才算数。
        CheckConstraint(
            "(batch_policy = 'CROSS_OK_WITHIN_TOL') = (delta_e_tolerance IS NOT NULL)",
            name="tolerance_iff_cross",
        ),
        Index("ix_sales_order_line_order", "order_no"),
        Index("ix_sales_order_line_product", "product_code"),
    )
