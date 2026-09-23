"""订单写端点与交期推演端点（P2.3.4）。

## 🔴 写端点不进工具集（宪法第二条）

``POST /api/v1/orders`` 由 LangGraph 下单子图直接调用，**不得**出现在导入 Dify 的
工具集里 —— 写路径只走 LangGraph。它带 ``tags=["写操作"]``，P2.3.5 导出 Dify 用的
OpenAPI 时按这个标签剔除；P2.4.4 的 API 文档则完整保留。

用标签而不是 ``include_in_schema=False``：后者会让写端点从 API 文档里也消失，
而「有哪些写操作」恰恰是审计与验收最想在文档里看到的东西。
"""

from typing import Annotated

from fastapi import APIRouter, Path, Response

from agentsystem.api import schemas
from agentsystem.api.envelope import Envelope
from agentsystem.db.session import app_session
from agentsystem.gateway.context import current_context
from agentsystem.orders.delay import assess_delay
from agentsystem.orders.service import create_order

#: 导出 Dify 工具集时据此剔除写端点。改名须同步 P2.3.5 的导出脚本。
WRITE_TAG = "写操作"

router = APIRouter(prefix="/api/v1")


@router.post(
    "/orders",
    summary="凭确认令牌创建订单",
    operation_id="create_order",
    tags=[WRITE_TAG],
    status_code=201,
    response_model=Envelope[schemas.OrderDetail],
    responses={200: {"description": "重复提交：未再执行，返回首次创建的订单"}},
)
async def post_order(
    body: schemas.CreateOrderRequest, response: Response
) -> Envelope[schemas.OrderDetail]:
    """执行一次已由用户二次确认的下单。

    请求体**只有 confirm_token**，业务载荷在确认时已存库（宪法第十条）。
    同一令牌重复提交返回 200 与原订单，不会再下一单（评分细则 S3-c）。
    """
    result = await create_order(confirm_token=body.confirm_token)
    if not result.created:
        response.status_code = 200
    return Envelope(data=result.order, trace_id=current_context().trace_id)


@router.get(
    "/orders/{order_no}/delay",
    summary="推演订单会不会延误",
    operation_id="query_order_delay",
    tags=["业务查询"],
    response_model=Envelope[schemas.DelayAssessment],
)
async def get_order_delay(
    order_no: Annotated[str, Path(description="订单号，如 SO-2026-000123")],
) -> Envelope[schemas.DelayAssessment]:
    """判断一张订单能否按客户要求的交期完成，并给出瓶颈工序与受牵连的下游工序。

    用户问「这张单会不会延期」「什么时候能交货」「延期会影响哪些工序」时使用。
    结论由服务端依排产计算，**直接引用 reasoning 字段作答，不要自行推算日期**。
    **不用于查订单当前状态** —— 那用 query_order。
    """
    async with app_session() as s:
        assessment = await assess_delay(s, order_no)
    return Envelope(data=assessment, trace_id=current_context().trace_id)
