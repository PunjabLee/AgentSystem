"""主数据：product / color / production_line（P2.1.1 · P2.1.2）。

这三张表是**自然语言到 code 的唯一映射入口**。用户说「查一下白色岩板的库存」，
而工具入参需要 ``product_code='P-B-2001'``；没有主数据表，这个映射只能靠 LLM
先去 RAG 里检索产品手册再拼 SQL —— 既不可靠，又把检索误差引进了本该确定的
查询路径。S2–S5 四个场景全部依赖本模块。

``production_line`` 还有一个单独的作用：没有它，``production_plan.changeover_min``
是死字段，S5 的「会不会延误」退化成两个日期比大小。有了产能才有真实推演：

    ETA = 该线队尾 plan_end
        + (队尾色号 ≠ 新单色号 ? changeover_min : 0)
        + qty / capacity_per_hour

**参照完整性**：本模块被三张业务表引用，但**不建外键**（[设计文档 §4.0]
的决策）。脏数据改由生成器的孤儿行断言、只读端点的 INNER JOIN、写端点的
存在性校验三者兜住。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentsystem.db.base import Base

#: 三个事业部。写成常量而非枚举类型，是因为它同时要出现在 users.yaml 的权限
#: 配置、工具的 schema 与 SQL 里 —— 单一来源比三处各写一遍可靠。
BU_CODES = ("BU-A", "BU-B", "BU-C")


class Product(Base):
    """产品主数据。自然语言检索的入口。"""

    __tablename__ = "product"

    product_code: Mapped[str] = mapped_column(
        String(32), primary_key=True, comment="P-A-1001 印染 / P-B-2001 瓷砖 / P-C-3001 洁具"
    )
    bu_code: Mapped[str] = mapped_column(String(8), nullable=False)
    product_name: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="「岩板 900×1800 素色系列」—— 自然语言检索入口"
    )
    category: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="坯布/印花布 · 岩板/瓷砖 · 坐便器/面盆/浴缸"
    )
    spec: Mapped[str] = mapped_column(String(64), nullable=False, comment="展示用规格字符串")

    # 结构化属性走 JSONB 而非拆成列（P2.1.6）：三个 BU 的属性真实发散 ——
    #   BU-A {"width_cm":150,"gsm":180,"yarn_count":"40S"}
    #   BU-B {"edge_mm":900,"thick_mm":9,"surface":"哑光"}
    #   BU-C {"glaze":"白釉","water_use_l":4.5}
    # 硬拆成列会让任意两个 BU 各有一半列恒为 NULL，且每加一个属性都要改表 ——
    # 而 audit_log 的教训是改表要走事件触发器解锁流程。
    # spec 仍保留展示字符串：给人看的和给机器算的分开，避免解析展示文本。
    attrs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), comment="按 BU 发散的结构化属性"
    )

    default_uom: Mapped[str] = mapped_column(
        String(8), nullable=False, comment="米/平方米/件 —— 业务表 uom 的权威来源"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'active'"), comment="active / discontinued"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_product_bu_category", "bu_code", "category"),
        # GIN on JSONB：支撑「厚度 9mm 以上的岩板」这类按属性过滤
        Index("ix_product_attrs", "attrs", postgresql_using="gin"),
        # GIN + trgm：支撑名称模糊匹配。**依赖 pg_trgm 扩展**，须超级用户预建
        # （scripts/06_extensions.sql）—— Alembic 以 app_migrator 运行，建不了扩展。
        Index(
            "ix_product_name_trgm",
            "product_name",
            postgresql_using="gin",
            postgresql_ops={"product_name": "gin_trgm_ops"},
        ),
    )


class Color(Base):
    """色号主数据。

    主键是 ``(bu_code, color_code)`` 而非单列：**同一个 color_code 在不同 BU 下
    可以指不同颜色** —— 三个事业部的色号体系各自独立，印染的 C-001 与瓷砖的
    C-001 毫无关系。若用单列主键，跨 BU 查询会把两种颜色合成一种。
    """

    __tablename__ = "color"

    bu_code: Mapped[str] = mapped_column(String(8), primary_key=True)
    color_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    color_name: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="「米白」「藏青」—— 自然语言检索入口"
    )
    color_family: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="白色系/深色系/彩色系，与色差判定的例外规则挂钩"
    )

    # 明度 L* < 30。QC-STD-006 §2.1：深色系印染品优等品的 ΔE 上限由 1.0 放宽到 1.5。
    # 存成布尔列而不是每次从 color_family 推断 —— 例外规则的判定不该依赖字符串匹配。
    is_dark: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), comment="明度 L* < 30"
    )
    std_sample_ref: Mapped[str | None] = mapped_column(
        String(64), comment="标准样编号；BU-C 统一为集团标准白度色板"
    )


class ProductionLine(Base):
    """产线产能。

    它的存在让 ``production_plan.changeover_min`` 从死字段变成真实计算：
    没有产能，「排产需计算换色换规格的清洗调机损耗」这句论证是空的。
    """

    __tablename__ = "production_line"

    line_code: Mapped[str] = mapped_column(
        String(32), primary_key=True, comment="染缸 D-01 / 窑炉 K-03 / 成型线 F-02"
    )
    bu_code: Mapped[str] = mapped_column(String(8), nullable=False)
    line_name: Mapped[str] = mapped_column(String(64), nullable=False)
    line_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="染缸/定型机 · 压机/窑炉/抛光线 · 注浆线/隧道窑"
    )
    # 产线所在区域。production_plan 的区域过滤经由本列 —— 计划本身不带
    # region，因为一条线不会跨区（P2.4.2）。
    region: Mapped[str] = mapped_column(String(16), nullable=False, comment="产线所在区域")
    capacity_per_hour: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False)

    # 🔴 产能带单位而计划量也带单位（production_plan.uom），两者必须可比。
    #    ETA 公式 qty / capacity_per_hour 隐含「单位一致」，数据库不校验它 ——
    #    生成器与只读端点须各自保证，见 §4.0 的补偿手段。
    capacity_uom: Mapped[str] = mapped_column(
        String(8), nullable=False, comment="米/小时 · 平方米/小时 · 件/小时"
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'running'"),
        comment="running / maintenance / idle",
    )

    __table_args__ = (
        Index("ix_production_line_bu_type", "bu_code", "line_type"),
        Index("ix_production_line_scope", "bu_code", "region"),
    )
