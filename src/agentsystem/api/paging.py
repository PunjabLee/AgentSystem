"""分页执行的共用部分（P2.3.3 的运行时配套）。

抽出来是因为「先 count 再取页」这件事，每个端点写一遍就有每个端点写错一次的机会 ——
最典型的错法是 count 时忘了带上同一套 WHERE，于是 ``total_count`` 比实际大，
模型据此说「共 120 条」而其实只有 12 条。
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentsystem.api.envelope import Envelope, paginated


async def run_paged(
    session: AsyncSession,
    stmt: Select,
    *,
    limit: int,
    offset: int,
    trace_id: str,
    to_model: Any,
    order_by: Sequence[Any],
    narrow_hint: str,
) -> Envelope[list[Any]]:
    """对一条已带全部过滤条件的 SELECT 执行计数与分页取数。

    Args:
        session: 业务会话。
        stmt: **已注入权限过滤**的查询（必须来自 ``scoped_select``）。
        limit: 页长，调用方须先过 ``clamp_limit``。
        offset: 偏移。
        trace_id: 全链路标识。
        to_model: 把一行 ORM 对象转成响应模型的可调用对象。
        order_by: 排序键序列，**最后一项必须是主键**（唯一）—— 见下。
        narrow_hint: 截断时给模型的收窄建议。

    Returns:
        带 ``pagination`` 与 ``notice`` 的包络。
    """
    # 🔴 count 必须基于同一条 stmt 派生，不能另写一条。另写的那条迟早会
    #    和主查询的 WHERE 走岔 —— 而走岔的表现是 total_count 偏大，
    #    模型照着说「共 120 条」，用户翻不到第 13 条。
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))

    # 排序键不是可选项：PostgreSQL 对无 ORDER BY 的 LIMIT/OFFSET 不保证
    # 行序稳定，翻页时同一行可能重复出现或整页漏掉，而**不会报任何错**。
    # **排序键不唯一时同理** —— 按 order_date 排序，同一天的几张单在两次翻页间
    # 可以互换位置。所以末项须是主键，把并列彻底打破。
    rows = (await session.execute(stmt.order_by(*order_by).limit(limit).offset(offset))).all()

    return paginated(
        [to_model(r) for r in rows],
        total=total or 0,
        limit=limit,
        offset=offset,
        trace_id=trace_id,
        narrow_hint=narrow_hint,
    )
