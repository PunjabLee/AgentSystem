"""权限注入的查询构造器（P2.4.1）。

## 为什么注入在 db 层而不是端点层

若在端点层拼 WHERE，**每个端点都要记得拼，漏一个就是越权**，而漏了不会报错、
不会有任何征兆 —— 它表现为「这个接口能查到别人的数据」，而没有任何测试会
自动发现。放在这里，端点函数的签名里根本没有 ``bu_codes`` / ``regions``，
想漏都漏不掉。

## 失败要关闭而不是打开

没有 :class:`RequestContext` 时 :func:`scoped_select` **抛错**，不是「不过滤」。
一个忘了设上下文的调用路径，若默认不过滤，就是一条静默的全量泄漏通道。

## 机械保障

``tests/test_permission_scope.py`` 断言：业务表的查询只能经本模块构造 ——
直接 ``select(SalesOrder)`` 会被检出。规则写在文档里靠自觉，写成检查才有效。
"""

from typing import Any

from sqlalchemy import Select, select

from agentsystem.gateway.context import current_context

#: 业务模型 → (BU 列名, 区域列名)。区域为 None 表示该表不按区域过滤。
#:
#: ``production_plan`` 的区域过滤经由 ``production_line`` —— 计划本身不带
#: region（一条线不会跨区），故这里标 None，其区域约束由端点 JOIN 产线时施加。
#: 这是本表**唯一**的例外，单独记在这里而不是散在端点里。
_SCOPE_COLUMNS: dict[str, tuple[str, str | None]] = {
    "sales_order": ("bu_code", "region"),
    "sales_order_line": ("__via_order__", None),  # 无自己的 bu/region，经订单头约束
    "inventory_batch": ("bu_code", "region"),
    "production_line": ("bu_code", "region"),
    "production_plan": ("bu_code", None),
    "product": ("bu_code", None),
    "color": ("bu_code", None),
}


class ScopeError(RuntimeError):
    """查询构造违反了权限注入约定。

    单列一个异常类型而不用 ValueError：它表示的是**编码错误**（有人给
    没登记 scope 的表建查询），应当在开发期就炸掉，不该被业务的 except 吞掉。
    """


def scoped_select(
    *entities: Any,
    table: str,
    bu: frozenset[str] | None = None,
    region: frozenset[str] | None = None,
) -> Select:
    """构造一个已注入权限过滤的 SELECT。

    Args:
        *entities: 要查的列或模型，同 :func:`sqlalchemy.select`。
        table: 被过滤的表名，用于查 :data:`_SCOPE_COLUMNS`。
        bu: 调用方请求的事业部范围；``None`` 表示未指定，取全部授权。
        region: 同上，按区域。

    Returns:
        已带 WHERE 的 Select。

    Raises:
        ScopeError: 表未登记 scope 列 —— 新表忘了登记时立刻炸，而不是
            悄悄少一层过滤。
        AuthError: 请求范围与授权无交集（越权）。**显式报错而非静默返回空**：
            静默会让用户以为「确实没有数据」，而真相是「你无权看」。
    """
    if table not in _SCOPE_COLUMNS:
        raise ScopeError(
            f"表 {table!r} 未在 _SCOPE_COLUMNS 登记。新增业务表必须登记其 "
            f"bu/region 列，否则查询会少一层权限过滤且无任何征兆。"
        )

    # 没有上下文就抛 —— 见模块 docstring「失败要关闭」。
    ctx = current_context()
    bu_col, region_col = _SCOPE_COLUMNS[table]
    stmt = select(*entities)

    if bu_col == "__via_order__":
        # 订单行没有自己的 bu/region，其范围由所属订单头决定。
        # 端点必须 JOIN sales_order 并对它施加过滤 —— 这里不能替它做，
        # 因为 JOIN 的形态由端点决定。故显式拒绝，逼调用方走订单头。
        raise ScopeError(
            "sales_order_line 不可直接 scoped_select：它没有自己的 bu/region，"
            "须 JOIN sales_order 并对订单头施加过滤。"
        )

    effective_bu = ctx.narrow_bu(bu)
    stmt = stmt.where(_column(table, bu_col).in_(sorted(effective_bu)))

    if region_col is not None:
        effective_region = ctx.narrow_region(region)
        # None 表示授权为通配且未指定 —— 不加条件即为不限。
        if effective_region is not None:
            stmt = stmt.where(_column(table, region_col).in_(sorted(effective_region)))
    elif region is not None:
        # 调用方对一张不带 region 的表指定了区域 —— 多半是拿错了表。
        # 静默忽略会让「按区域筛」看起来生效了，实际没有。
        raise ScopeError(f"表 {table!r} 无 region 列，不能按区域过滤")

    return stmt


def _column(table: str, name: str):
    """按表名与列名取 SQLAlchemy 列对象。"""
    from agentsystem.db.base import Base

    return Base.metadata.tables[table].c[name]
