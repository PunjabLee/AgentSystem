"""工具 schema 与响应模型（P2.3.4，契约见 P2 详细设计 §2 / §3）。

## 这个文件是 function calling 准确率杠杆最大的地方

评审原话：「``grade`` 声明成带 description 的 enum 还是自由字符串，对
≥90% 端到端成功率的影响远大于大多数 prompt 调整。」所以这里的每个
``Field(description=...)`` 都是**写给模型看的**，不是给人看的注释：

* **工具描述回答「什么时候该选它」**，不是「它做什么」。五个工具的正向
  描述天然相似，末句的**负向说明**（「不用于……」）比正向描述更能减少误选。
* **参数描述写取值示例与约束**，不复述参数名。`region: "区域"` 是零信息。
* **枚举逐值解释**，且把「用户未明确时须追问」这类澄清义务写进 description ——
  它离参数最近，比在 system prompt 里统一交代可靠。
* **工具间的调用顺序也写进 description**（「先调 query_product 取编码」），
  这是让模型正确串联多步查询最省事的办法。

## 🔴 三类参数绝对不出现在这里

宪法第十条：``user_id`` / ``session_id`` / ``tenant`` **绝对禁止**入参 ——
身份不能由模型声明。``bu_code`` / ``region`` 可以出现，但**只用于在已有权限
内收窄**，上界由服务端按身份注入（``db/scope.py``），越界显式报错。

实现时最容易违反的地方是「顺手加个 bu_code 参数让模型指定，省得服务端判断」。
写 schema 时若发现某个参数能让模型跨越数据边界，它就该从 schema 里删掉。
"""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# ── 枚举 ──────────────────────────────────────────────────────────
# 一律 Literal 而非 str（§2.3）。取值**以数据库 CHECK 约束为准**，
# 不以设计文档为准 —— 两者不一致时错的是文档：约束是数据真正满足的那个，
# 而 Literal 写错的表现是「查询永远返回 0 行且不报错」。

#: 品级。与 ``inventory_batch.grade`` 的 CHECK 约束同值域。
Grade = Literal["优等品", "一等品", "合格品"]

#: 批次策略。与 ``sales_order_line.batch_policy`` 的 CHECK 约束同值域。
BatchPolicy = Literal["SAME_BATCH", "CROSS_OK_WITHIN_TOL", "ANY"]

#: 订单状态。
#:
#: ⚠️ 设计文档 §2.3 写的是 ``待确认`` / ``已发货``，而数据库 CHECK 约束是
#: ``待评审`` / ``部分发货``，实际数据也是后者。此处以约束为准 ——
#: 照文档写会让 ``query_order(status="待确认")`` 永远返回 0 行，且不报错。
OrderStatus = Literal["待评审", "已确认", "生产中", "部分发货", "已完成", "已取消"]

#: 排产状态。与 ``production_plan.status`` 的 CHECK 约束同值域。
PlanStatus = Literal["待排", "已排", "生产中", "已完成", "暂停"]

#: 质检状态。``inventory_batch.qc_status`` 无 CHECK 约束，取值取自实际数据。
QcStatus = Literal["待检", "合格", "让步接收", "不合格"]


# ── 收窄用的范围参数 ──────────────────────────────────────────────
# 这两个是**唯一**允许模型传的范围参数，且语义严格是「在已有权限内收窄」。

BuCode = Annotated[
    str | None,
    Field(
        description=(
            "事业部编码，BU-A=印染 / BU-B=建陶瓷砖 / BU-C=卫浴洁具。"
            "仅用于在你已有权限范围内收窄查询，**不能用于访问其他事业部** —— "
            "越界会被服务端拒绝并记入审计。留空则返回你有权查看的全部事业部。"
        ),
        examples=["BU-B"],
    ),
]

Region = Annotated[
    str | None,
    Field(
        description=(
            "区域，如「华东」。仅用于在你已有权限范围内收窄查询，"
            "不能用于访问其他区域 —— 越界会被服务端拒绝。"
            "留空则返回你有权查看的全部区域。"
        ),
        examples=["华东"],
    ),
]


# ── 响应模型 ──────────────────────────────────────────────────────


class Product(BaseModel):
    """产品主数据的一条。"""

    product_code: str = Field(description="产品编码，格式 P-{BU字母}-{4位数字}")
    bu_code: str
    product_name: str = Field(description="产品名称，自然语言检索的落点")
    category: str = Field(description="品类：坯布/印花布 · 岩板/瓷砖 · 坐便器/面盆/浴缸")
    spec: str = Field(description="展示用规格字符串，如「900×1800×9mm」")
    attrs: dict = Field(description="结构化属性，按事业部发散：门幅克重 / 边长厚度 / 釉面用水量")
    default_uom: str = Field(description="默认计量单位：米 / 平方米 / 件")
    status: str = Field(description="active=在售，discontinued=已停产")


class InventoryBatch(BaseModel):
    """一个可发货批次。"""

    batch_id: int
    warehouse_code: str
    region: str
    bu_code: str
    product_code: str
    batch_no: str = Field(description="染缸号 D2601-08 / 窑批号 K2603-15 / 注浆批号 J2602-04")
    color_code: str
    grade: Grade
    spec: str
    delta_e: float | None = Field(
        default=None,
        description=(
            "本批相对**标准大样**的色差，不是批与批之间的色差。"
            "判断两批能否拼单要比较两批 delta_e 的**差值**是否在容差内；"
            "直接拿单批的 delta_e 与订单行容差比较是错的。"
        ),
    )
    qty_available: float = Field(description="可用量，单位见 uom")
    uom: str
    inbound_date: date | None = None
    qc_status: QcStatus | None = Field(
        default=None,
        description="待检=尚未检验不可发货；合格=可发；让步接收=降级可发；不合格=不可发",
    )


class OrderLine(BaseModel):
    """订单行。"""

    line_no: int
    product_code: str
    spec: str
    color_code: str
    grade_required: Grade
    batch_policy: BatchPolicy = Field(
        description=(
            "SAME_BATCH=必须同一染缸/窑批供货，库存不足时不得拼单；"
            "CROSS_OK_WITHIN_TOL=可跨批但两批间 ΔE 差值须 ≤ delta_e_tolerance；"
            "ANY=不限，各批单独满足等级即可。"
        )
    )
    delta_e_tolerance: float | None = Field(
        default=None, description="仅当 batch_policy=CROSS_OK_WITHIN_TOL 时有值"
    )
    qty: float
    uom: str
    unit_price: float | None = None


class OrderBrief(BaseModel):
    """订单头。"""

    order_no: str = Field(description="订单号，格式 SO-{年份}-{6位数字}")
    bu_code: str
    region: str
    customer_code: str
    customer_name: str
    order_type: str
    order_date: date
    required_date: date = Field(description="客户要求交期")
    promised_date: date | None = Field(default=None, description="承诺交期")
    status: OrderStatus
    total_amount: float | None = None
    policy_code: str | None = Field(default=None, description="适用的营销政策编码")


class OrderDetail(OrderBrief):
    """订单头 + 订单行。创建订单的返回体。"""

    lines: list[OrderLine] = Field(default_factory=list)
    created_at: datetime | None = None


class ProductionPlan(BaseModel):
    """一条产线上的一道工序计划。"""

    plan_no: str
    bu_code: str
    line_code: str
    product_code: str
    color_code: str
    process_stage: str = Field(description="工序名：前处理/染色/后整理 或 压机/窑炉/抛光/分级")
    stage_seq: int = Field(description="工序顺序号，延期沿本字段递增方向传播")
    planned_qty: float
    qty_completed: float
    uom: str
    plan_start: datetime
    plan_end: datetime
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    changeover_min: int = Field(description="换色/换规格调机时长（分钟）")
    status: PlanStatus
    related_order_line: int | None = Field(
        default=None, description="关联的订单行 line_id；备货型计划为空"
    )


class DelayAssessment(BaseModel):
    """交期延误推演（S5）。

    ``reasoning`` 是**服务端算好的自然语言结论**，不是让模型自己推。
    延误判定涉及工序顺序、产能、换型损耗，交给模型算必然出错；
    服务端算完给结论，模型只负责组织表达。
    """

    order_no: str
    required_date: date = Field(description="客户要求交期")
    estimated_completion: datetime | None = Field(
        default=None, description="末道工序的计划完成时间（按 stage_seq 取最后一道）"
    )
    will_delay: bool
    delay_days: int = Field(description="延误天数；**负数表示提前**")
    bottleneck_stage: str | None = Field(default=None, description="造成延误的工序")
    # 设计文档 §3.1 的契约里没有这一列，**增补**的理由与 reasoning 相同：
    # 评分细则 S5-d「延期影响哪些下游」是本项目最难的一条，交给模型从排产列表
    # 自己推，它要同时理解订单行粒度与 stage_seq 两件事 —— 服务端算好直接给。
    affected_downstream: list[str] = Field(
        default_factory=list,
        description="受瓶颈工序牵连而顺延的下游计划编号（同一订单行、工序顺序更靠后）",
    )
    reasoning: str = Field(description="人类可读的推演说明，可直接引用给用户")


class CreateOrderRequest(BaseModel):
    """创建订单的请求体。

    🔴 **只有令牌，没有任何业务字段。** 这是宪法第十条的直接要求：
    合法令牌配一份篡改过的载荷即可绕过二次确认 —— 重放防住了，参数篡改没防。
    业务载荷在用户确认时已存入 ``write_intent.payload``，执行时只从库里取。

    ``extra="forbid"`` 不可省：pydantic 默认**静默忽略**多余字段，于是带着
    ``qty=99999`` 的请求会返回 201 —— 篡改虽未生效，但评分细则 S3-b 要求
    「带业务参数的请求须被拒」，而且静默接受会让调用方误以为参数起了作用。
    """

    model_config = ConfigDict(extra="forbid")

    confirm_token: str = Field(
        description="用户二次确认时服务端签发的一次性令牌。缺失或无效一律拒绝执行。",
        min_length=16,
        max_length=64,
    )
