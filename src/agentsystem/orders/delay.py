"""交期延误推演（S5，契约见 P2 详细设计 §3.1）。

## 🔒 判定式与冻结的评分细则逐字对齐

评分细则 S5-c（commit ``4845d19`` 冻结）规定：延误结论须与标准 SQL
``末道工序 plan_end > required_date`` 一致。所以 ``will_delay`` **在 SQL 里用同一个
表达式算**，不在 Python 里另写一版 ——

  * 另写一版就要论证两者等价（``timestamptz > date`` 的隐式转换、时区、
    ``.date()`` 截断），论证错一处结论就会系统性偏离判据；
  * 更根本的是：细则在实现之前冻结，就是为了不让标准向实现靠拢。若这里发明一个
    「更聪明」的模型（考虑实绩滑移、换型损耗再向下游传播），它的结论会与冻结判据
    不一致，而那时要改的不是判据。

更聪明的推演（实绩滑移传播、换型损耗）只进 ``reasoning`` 作**解释**，不改结论。

## 瓶颈与联动范围

* **瓶颈工序**：关键订单行上**第一个**计划结束晚于交期的工序 —— 排期在这里
  突破了交期，其后的工序只是被它带晚。
* **联动范围**（S5-d）：与瓶颈同一订单行、``stage_seq`` 更大的计划。这是细则
  S5-d 的原文定义，服务端算好给出，不让模型去推。
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text

from agentsystem.api import schemas
from agentsystem.db.models.order import SalesOrder
from agentsystem.db.scope import scoped_select
from agentsystem.errors import ValidationError

#: 业务时区。交期是**本地日历日**，排产时刻是 timestamptz。
#:
#: 🔴 数据库与 asyncpg 会话都是 UTC，而 plan_end 存的是上海零点这个时刻
#:    （= UTC 前一天 16:00）。直接对它取 ``.date()`` 会得到前一天 —— 实测
#:    排期晚 5 天的单被算成「延误 4 天」，说明文字里的时间也显示成 16:00。
#:    判定（时刻比较）不受影响，受影响的是**说给用户听的那句话**。
BUSINESS_TZ = ZoneInfo("Asia/Shanghai")

#: 取某订单全部排产，含「是否晚于交期」的逐行判定。
#:
#: 🔴 ``p.plan_end > o.required_date`` 与评分细则 S5-c 的标准 SQL 是同一表达式，
#:    不要改写成 ``p.plan_end::date > o.required_date`` —— 对非零点的 plan_end
#:    两者结论不同，而细则用的是前者。
#:
#: 两处 JOIN 均为 INNER（不建外键的补偿手段①）：孤儿计划不进推演。
_PLANS_SQL = text(
    """
    SELECT l.line_id, l.line_no, p.plan_no, p.process_stage, p.stage_seq,
           p.plan_end, p.changeover_min,
           (p.plan_end > o.required_date) AS late
      FROM sales_order o
      JOIN sales_order_line l ON l.order_no = o.order_no
      JOIN production_plan  p ON p.related_order_line = l.line_id
     WHERE o.order_no = :order_no
     ORDER BY l.line_no, p.stage_seq
    """
)


async def assess_delay(s, order_no: str) -> schemas.DelayAssessment:
    """推演一张订单会不会延误。

    Raises:
        ValidationError: 订单不存在、无权查看，或尚无任何排产。
    """
    # 先经权限层确认订单可见。下面那条原生 SQL 不经 scoped_select，
    # 必须有这一步挡在前面 —— 否则按订单号就能推演别人的订单。
    head = (
        await s.execute(
            scoped_select(SalesOrder.order_no, SalesOrder.required_date, table="sales_order").where(
                SalesOrder.order_no == order_no
            )
        )
    ).one_or_none()
    if head is None:
        # 不区分「不存在」与「无权看」：区分了就能拿订单号探测别人的订单是否存在。
        raise ValidationError(f"未找到订单 {order_no}", code="VAL_ORDER_NOT_FOUND")
    required: date = head.required_date

    rows = (await s.execute(_PLANS_SQL, {"order_no": order_no})).all()
    if not rows:
        raise ValidationError(f"订单 {order_no} 尚无排产计划，无法推演交期", code="VAL_NO_PLAN")

    # 每个订单行的末道工序（ORDER BY 已按 stage_seq 升序，最后一个即末道）
    last_by_line: dict[int, object] = {}
    for r in rows:
        last_by_line[r.line_id] = r
    finish = max(last_by_line.values(), key=lambda r: r.plan_end)
    estimated: datetime = finish.plan_end
    will_delay = any(r.late for r in last_by_line.values())

    bottleneck = None
    downstream: list[str] = []
    if will_delay:
        # 关键行：末道工序最晚的那一行；其上第一个晚于交期的工序即瓶颈。
        chain = [r for r in rows if r.line_id == finish.line_id]
        bottleneck = next(r for r in chain if r.late)
        downstream = [r.plan_no for r in chain if r.stage_seq > bottleneck.stage_seq]

    delay_days = (estimated.astimezone(BUSINESS_TZ).date() - required).days
    return schemas.DelayAssessment(
        order_no=order_no,
        required_date=required,
        estimated_completion=estimated,
        will_delay=will_delay,
        delay_days=delay_days,
        bottleneck_stage=bottleneck.process_stage if bottleneck else None,
        affected_downstream=downstream,
        reasoning=_explain(
            order_no, required, estimated, will_delay, delay_days, bottleneck, downstream, rows
        ),
    )


def _explain(order_no, required, estimated, will_delay, delay_days, bottleneck, downstream, rows):
    """生成可直接引用给用户的推演说明。

    换型损耗在这里**只作解释**：它已计入各工序的计划窗口，不再叠加到结论上
    （叠加会让结论偏离冻结的判据 S5-c）。但用户问「为什么这么晚」时，
    它往往是答案的一部分，所以要说出来。
    """
    stages = len({r.plan_no for r in rows})
    changeover = sum(r.changeover_min for r in rows)
    head = (
        f"订单 {order_no} 客户要求 {required.isoformat()} 交货，"
        f"共 {stages} 道排产工序，"
        f"末道工序计划于 {estimated.astimezone(BUSINESS_TZ):%Y-%m-%d %H:%M} 完成。"
    )
    if not will_delay:
        return head + f"预计按期交付，较交期提前 {-delay_days} 天。"
    tail = (
        f"预计延误 {delay_days} 天。排期在「{bottleneck.process_stage}」"
        f"（第 {bottleneck.stage_seq} 道工序，计划 {bottleneck.plan_no}）首次突破交期"
    )
    tail += f"，其后 {len(downstream)} 道工序随之顺延。" if downstream else "，且它已是末道工序。"
    if changeover:
        tail += f"各工序计划内含换色/换规格调机共 {changeover} 分钟。"
    return head + tail
