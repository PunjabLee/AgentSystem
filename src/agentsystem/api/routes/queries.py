"""五类查询端点中的四个 SQL 端点（P2.3.4，契约见 P2 详细设计 §3）。

## 🔴 不建外键的补偿手段①：JOIN 一律 INNER

九张业务表**一律无外键**（设计文档 §4.0 决策）。该决策成立的**前提**之一是
「只读端点的 JOIN 一律 ``INNER JOIN``」—— 孤儿行自然不进结果。

用 LEFT JOIN 会把指向已不存在的父行的孤儿行带进结果，字段一片 NULL，
而模型会把 NULL 当成「这个订单没有客户」如实讲给用户。这不是可选的编码风格，
是该决策的构成部分。

## 关于 query_policy

设计文档 §3 列了五个工具，但 ``query_policy`` **在这里没有实现** ——
九张表里没有营销政策表，而 ``docs/eval/FIXTURE-SPEC.md`` 明写「S1 不依赖业务表
（走 RAG）」。它属于 P3 的知识库检索，不是 SQL 端点。详见 OPEN-ITEMS。
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Path, Query
from sqlalchemy import and_

from agentsystem.api import schemas
from agentsystem.api.envelope import Envelope, clamp_limit
from agentsystem.api.paging import run_paged
from agentsystem.api.schemas import BuCode, Grade, OrderStatus, Region
from agentsystem.db.models.inventory import InventoryBatch
from agentsystem.db.models.master import Product, ProductionLine
from agentsystem.db.models.order import SalesOrder
from agentsystem.db.models.plan import ProductionPlan
from agentsystem.db.scope import scoped_select
from agentsystem.db.session import app_session
from agentsystem.errors import ValidationError
from agentsystem.gateway.context import current_context

router = APIRouter(prefix="/api/v1", tags=["业务查询"])

#: 页长参数。四个端点共用，避免各写各的上限。
LimitQ = Annotated[int | None, Query(description="每页条数，默认 20，上限 100。超出会被静默收窄。")]
OffsetQ = Annotated[int, Query(ge=0, description="偏移量，从 0 开始。")]


def _scope(bu: str | None, region: str | None) -> tuple[frozenset[str] | None, ...]:
    """把单值的范围参数转成 ``scoped_select`` 要的集合形态。"""
    return (
        frozenset({bu}) if bu else None,
        frozenset({region}) if region else None,
    )


@router.get(
    "/products",
    summary="按名称关键词查产品",
    operation_id="query_product",
    response_model=Envelope[list[schemas.Product]],
)
async def query_product(
    keyword: Annotated[
        str | None,
        Query(
            description=(
                "产品名关键词，如「岩板」「白坯布」。模糊匹配，不区分大小写。"
                "**这是自然语言名称转产品编码的入口** —— 用户只说了产品名时先调它拿编码，"
                "再用编码去查库存或排产。"
            )
        ),
    ] = None,
    category: Annotated[
        str | None,
        Query(description="品类精确匹配：坯布/印花布 · 岩板/瓷砖 · 坐便器/面盆/浴缸"),
    ] = None,
    bu_code: BuCode = None,
    limit: LimitQ = None,
    offset: OffsetQ = 0,
) -> Envelope[list[schemas.Product]]:
    """按名称或品类检索产品主数据，拿到 product_code。

    用户提到产品但没给编码时用它。**不用于查这个产品还有多少货** ——
    那属于库存查询。
    """
    bu, _ = _scope(bu_code, None)
    limit = clamp_limit(limit)
    stmt = scoped_select(Product, table="product", bu=bu)
    if keyword:
        stmt = stmt.where(Product.product_name.ilike(f"%{keyword}%"))
    if category:
        stmt = stmt.where(Product.category == category)

    async with app_session() as s:
        return await run_paged(
            s,
            stmt,
            limit=limit,
            offset=offset,
            trace_id=current_context().trace_id,
            to_model=lambda r: schemas.Product.model_validate(r[0], from_attributes=True),
            order_by=(Product.product_code,),
            narrow_hint="补充品类或更具体的产品名关键词",
        )


@router.get(
    "/inventory",
    summary="查实时库存批次",
    operation_id="query_inventory",
    response_model=Envelope[list[schemas.InventoryBatch]],
)
async def query_inventory(
    product_code: Annotated[
        str | None,
        Query(
            description=(
                "产品编码，格式 P-{BU字母}-{4位数字}，如 P-B-2001。"
                "若用户只说了产品名（如「岩板」），**先调 query_product 取编码**。"
            )
        ),
    ] = None,
    color_code: Annotated[str | None, Query(description="色号，如 C-B-012")] = None,
    warehouse_code: Annotated[str | None, Query(description="仓库编码，如 WH-B-01")] = None,
    batch_no: Annotated[
        str | None,
        Query(description="批次号：染缸 D2601-08 / 窑批 K2603-15 / 注浆批 J2602-04，精确匹配"),
    ] = None,
    grade: Annotated[
        Grade | None,
        Query(description="品级：优等品 > 一等品 > 合格品。留空则返回全部品级。"),
    ] = None,
    bu_code: BuCode = None,
    region: Region = None,
    limit: LimitQ = None,
    offset: OffsetQ = 0,
) -> Envelope[list[schemas.InventoryBatch]]:
    """按产品、色号、仓库、批次、品级维度查实时库存。

    用户询问某产品还有多少货、哪个仓库有货、某批次的等级与色差时使用。
    **不用于查询在产数量** —— 那属于排产查询（query_production_plan）。
    """
    bu, reg = _scope(bu_code, region)
    limit = clamp_limit(limit)
    stmt = scoped_select(InventoryBatch, table="inventory_batch", bu=bu, region=reg)
    for column, value in (
        (InventoryBatch.product_code, product_code),
        (InventoryBatch.color_code, color_code),
        (InventoryBatch.warehouse_code, warehouse_code),
        (InventoryBatch.batch_no, batch_no),
        (InventoryBatch.grade, grade),
    ):
        if value:
            stmt = stmt.where(column == value)

    async with app_session() as s:
        return await run_paged(
            s,
            stmt,
            limit=limit,
            offset=offset,
            trace_id=current_context().trace_id,
            to_model=lambda r: schemas.InventoryBatch.model_validate(r[0], from_attributes=True),
            # 批次多时先看可用量大的：拼单判断关心的是「哪几批凑得够」。
            order_by=(InventoryBatch.qty_available.desc(), InventoryBatch.batch_id),
            narrow_hint="指定色号、仓库或品级",
        )


@router.get(
    "/orders",
    summary="查销售订单",
    operation_id="query_order",
    response_model=Envelope[list[schemas.OrderBrief]],
)
async def query_order(
    order_no: Annotated[
        str | None,
        Query(description="订单号 SO-2026-000123，精确匹配。已知订单号时优先用它。"),
    ] = None,
    customer_code: Annotated[str | None, Query(description="客户编码，如 CUST-B-007")] = None,
    status: Annotated[
        OrderStatus | None,
        Query(
            description=(
                "订单状态。待评审=已录入未确认；已确认=已确认未开工；"
                "生产中=在产；部分发货=已发一部分；已完成=全部交付；已取消=作废。"
            )
        ),
    ] = None,
    date_from: Annotated[
        date | None, Query(description="下单日期下界，ISO 8601 如 2026-01-01")
    ] = None,
    date_to: Annotated[date | None, Query(description="下单日期上界（含），ISO 8601")] = None,
    bu_code: BuCode = None,
    region: Region = None,
    limit: LimitQ = None,
    offset: OffsetQ = 0,
) -> Envelope[list[schemas.OrderBrief]]:
    """按订单号、客户、状态、下单日期区间查订单。

    用户问某张订单到哪一步了、某客户最近下了哪些单时使用。
    **不用于查这张单什么时候能做完** —— 那要看排产（query_production_plan）。
    """
    bu, reg = _scope(bu_code, region)
    limit = clamp_limit(limit)
    stmt = scoped_select(SalesOrder, table="sales_order", bu=bu, region=reg)
    if order_no:
        stmt = stmt.where(SalesOrder.order_no == order_no)
    if customer_code:
        stmt = stmt.where(SalesOrder.customer_code == customer_code)
    if status:
        stmt = stmt.where(SalesOrder.status == status)
    if date_from:
        stmt = stmt.where(SalesOrder.order_date >= date_from)
    if date_to:
        stmt = stmt.where(SalesOrder.order_date <= date_to)

    async with app_session() as s:
        return await run_paged(
            s,
            stmt,
            limit=limit,
            offset=offset,
            trace_id=current_context().trace_id,
            to_model=lambda r: schemas.OrderBrief.model_validate(r[0], from_attributes=True),
            order_by=(SalesOrder.order_date.desc(), SalesOrder.order_no),
            narrow_hint="指定客户、状态或更窄的日期区间",
        )


@router.get(
    "/production-plans",
    summary="查排产计划",
    operation_id="query_production_plan",
    response_model=Envelope[list[schemas.ProductionPlan]],
)
async def query_production_plan(
    order_no: Annotated[
        str | None,
        Query(
            description=(
                "订单号 SO-2026-000123。传入则只返回该订单牵动的排产。"
                "要回答「这张单会不会延期」请改用 /api/v1/orders/{order_no}/delay。"
            )
        ),
    ] = None,
    line_code: Annotated[str | None, Query(description="产线编码，如 LINE-B-02")] = None,
    date_from: Annotated[date | None, Query(description="计划开始时间下界，ISO 8601")] = None,
    date_to: Annotated[date | None, Query(description="计划结束时间上界，ISO 8601")] = None,
    bu_code: BuCode = None,
    limit: LimitQ = None,
    offset: OffsetQ = 0,
) -> Envelope[list[schemas.ProductionPlan]]:
    """按订单、产线、时间区间查排产计划。

    用户问某条线在排什么、某订单安排在哪几道工序、产能排到什么时候时使用。
    **不用于查成品库存** —— 在产数量与可发库存是两回事。
    """
    bu, _ = _scope(bu_code, None)
    limit = clamp_limit(limit)
    stmt = _plan_base_query(bu)

    if order_no:
        # 🔴 INNER JOIN（补偿手段①）：订单行与计划之间无外键，LEFT JOIN 会把
        #    指向已删订单行的孤儿计划带进来，模型会把它当成「这张单的排产」。
        stmt = _join_order(stmt).where(SalesOrder.order_no == order_no)
    if line_code:
        stmt = stmt.where(ProductionPlan.line_code == line_code)
    if date_from:
        stmt = stmt.where(ProductionPlan.plan_start >= date_from)
    if date_to:
        stmt = stmt.where(ProductionPlan.plan_end <= date_to)

    async with app_session() as s:
        return await run_paged(
            s,
            stmt,
            limit=limit,
            offset=offset,
            trace_id=current_context().trace_id,
            to_model=lambda r: schemas.ProductionPlan.model_validate(r[0], from_attributes=True),
            # 先按产线再按计划结束时间：这正是「某条线的队列」的自然顺序。
            order_by=(ProductionPlan.line_code, ProductionPlan.plan_end, ProductionPlan.plan_no),
            narrow_hint="指定产线或更窄的时间区间",
        )


def _plan_base_query(bu: frozenset[str] | None):
    """排产计划的基础查询，含区域权限的补丁式注入。

    ``production_plan`` 在 ``_SCOPE_COLUMNS`` 里区域列登记为 ``None``（一条线
    不会跨区，计划本身不带 region），所以 ``scoped_select`` 只注入了 BU 过滤。
    **区域约束必须由本函数补上**，否则一个只授权华东的用户能看到全国的排产 ——
    而这既不会报错也没有任何征兆。

    补法是 INNER JOIN ``production_line`` 并对产线的 region 施加过滤。
    """
    stmt = scoped_select(ProductionPlan, table="production_plan", bu=bu)
    ctx = current_context()
    effective_region = ctx.narrow_region(None)
    if effective_region is None:
        # 授权为通配，不需要额外约束，也就不必付这次 JOIN 的代价。
        return stmt
    return stmt.join(ProductionLine, ProductionPlan.line_code == ProductionLine.line_code).where(
        ProductionLine.region.in_(sorted(effective_region))
    )


def _join_order(stmt):
    """把计划连到订单头（经订单行），并对订单头施加同一套权限过滤。

    两处 INNER JOIN 都是补偿手段①。对订单头**再施加一次**权限过滤不是冗余：
    计划的 bu_code 与其关联订单的 bu_code 理论上一致，但没有外键保证，
    一旦数据错乱就会让越权从关联路径渗出来。
    """
    from agentsystem.db.models.order import SalesOrderLine

    ctx = current_context()
    joined = stmt.join(
        SalesOrderLine, ProductionPlan.related_order_line == SalesOrderLine.line_id
    ).join(SalesOrder, SalesOrderLine.order_no == SalesOrder.order_no)
    conditions = [SalesOrder.bu_code.in_(sorted(ctx.narrow_bu(None)))]
    effective_region = ctx.narrow_region(None)
    if effective_region is not None:
        conditions.append(SalesOrder.region.in_(sorted(effective_region)))
    return joined.where(and_(*conditions))


@router.get(
    "/production-plans/{plan_no}/downstream",
    summary="查某道工序延期会牵连哪些下游工序",
    operation_id="query_plan_downstream",
    response_model=Envelope[list[schemas.ProductionPlan]],
)
async def query_plan_downstream(
    plan_no: Annotated[
        str,
        Path(description="排产计划编号，如 PP-000123。先用 query_production_plan 按订单查出来。"),
    ],
) -> Envelope[list[schemas.ProductionPlan]]:
    """回答「这道工序要是延期了，会影响后面哪几道」。

    返回与该计划**同一订单行**、工序顺序更靠后的全部计划，按工序顺序排列。
    延期沿工序顺序向后传播，所以这就是受牵连的完整集合 —— **直接用它作答，
    不要自己从排产列表里推**。不用于判断订单会不会延误 —— 那用 query_order_delay。

    这个工具不在设计文档 §3 的五个工具里，是为评分细则 S5-d 增补的：
    「联动范围」要同时理解订单行粒度与 stage_seq，交给模型推正是细则担心的环节。
    """
    ctx = current_context()
    async with app_session() as s:
        src = (
            await s.execute(_plan_base_query(None).where(ProductionPlan.plan_no == plan_no))
        ).scalar_one_or_none()
        if src is None:
            # 不区分「不存在」与「无权看」，理由同订单推演端点。
            raise ValidationError(f"未找到排产计划 {plan_no}", code="VAL_PLAN_NOT_FOUND")
        if src.related_order_line is None:
            return Envelope(
                data=[],
                notice="该计划是备货型计划，不关联任何订单行，因此没有同订单的下游工序。",
                trace_id=ctx.trace_id,
            )
        rows = (
            await s.execute(
                _plan_base_query(None)
                .where(ProductionPlan.related_order_line == src.related_order_line)
                .where(ProductionPlan.stage_seq > src.stage_seq)
                .order_by(ProductionPlan.stage_seq, ProductionPlan.plan_no)
            )
        ).scalars()
        data = [schemas.ProductionPlan.model_validate(r, from_attributes=True) for r in rows]
    notice = None if data else f"{plan_no} 已是该订单行的末道工序，延期不会牵连其他工序。"
    return Envelope(data=data, notice=notice, trace_id=ctx.trace_id)
