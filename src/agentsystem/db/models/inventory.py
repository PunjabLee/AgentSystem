"""库存批次：inventory_batch（P2.1.4）。

批次是拼批决策的最小单位。S2「查库存」的真正难点不是求和，而是**这些批次
能不能拼给这一行订单** —— 取决于订单行的 ``batch_policy`` 与批次的色差。
"""

from datetime import date

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from agentsystem.db.base import Base

GRADES = ("优等品", "一等品", "合格品")
QC_STATUSES = ("待检", "合格", "让步接收", "不合格")


class InventoryBatch(Base):
    """一个可发货批次。"""

    __tablename__ = "inventory_batch"

    batch_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    warehouse_code: Mapped[str] = mapped_column(String(16), nullable=False)
    bu_code: Mapped[str] = mapped_column(String(8), nullable=False)
    product_code: Mapped[str] = mapped_column(String(32), nullable=False)
    batch_no: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="染缸号 D2601-08 / 窑批号 K2603-15"
    )
    color_code: Mapped[str] = mapped_column(String(32), nullable=False)
    grade: Mapped[str] = mapped_column(String(16), nullable=False, comment="优等品/一等品/合格品")
    spec: Mapped[str] = mapped_column(String(64), nullable=False)

    # 🔴 ΔE 的基准是**本批 vs 标准大样**，不是批与批之间。
    #    跨缸拼单要比的是两批各自 ΔE 的**差值**（QC-STD-006 §2.3）——
    #    直接拿两行的 delta_e 相减在数学上恰好是对的，但拿单行的 delta_e
    #    去和订单行的 delta_e_tolerance 比是**错的**：那是在拿「对大样的偏差」
    #    冒充「批间偏差」。这条注释是为了防住后者。
    delta_e: Mapped[float | None] = mapped_column(Numeric(5, 2), comment="本批 vs 标准大样")

    qty_available: Mapped[float] = mapped_column(Numeric(14, 3), nullable=False)

    # P2 不做库存预占，本列恒为 0，预留给未来的预占语义。
    # 可用量一律用 qty_available，**不要**写成 qty_available - qty_locked ——
    # 那会在本期得到同样的结果，却在预占上线那天悄悄改变所有查询的含义。
    qty_locked: Mapped[float] = mapped_column(
        Numeric(14, 3), nullable=False, server_default=text("0"), comment="P2 恒为 0，预留"
    )

    # 与 sales_order_line.uom / product.default_uom 同值域。缺它则库存量与
    # 订单量不可比 —— 而「够不够发」正是 S2 要回答的。
    uom: Mapped[str] = mapped_column(String(8), nullable=False)

    inbound_date: Mapped[date | None] = mapped_column(Date)
    qc_status: Mapped[str | None] = mapped_column(String(16), comment="待检/合格/让步接收/不合格")

    __table_args__ = (
        # 同一仓同一产品同一批同色同级只应有一行。缺它则同一批次可被拆成多行，
        # 求和虚高而拼批判定失真。
        UniqueConstraint(
            "warehouse_code",
            "product_code",
            "batch_no",
            "color_code",
            "grade",
            name="uq_inventory_batch_identity",
        ),
        CheckConstraint("grade IN ('优等品', '一等品', '合格品')", name="grade_enum"),
        CheckConstraint("qty_available >= 0", name="qty_nonneg"),
        Index("ix_inventory_batch_lookup", "bu_code", "product_code", "color_code"),
    )
