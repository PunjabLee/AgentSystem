"""权限注入的断言（P2.4.1 · 宪法第十条）。

三条红线里最容易悄悄失效的一条：漏一个 WHERE 不会报错、不会有征兆，
只表现为「这个接口能查到别人的数据」。故本文件既测行为，也做机械检查。
"""

import ast
import pathlib

import pytest
from sqlalchemy import text

from agentsystem.db.models.master import Product  # noqa: F401 —— 触发 metadata 注册
from agentsystem.db.scope import ScopeError, scoped_select
from agentsystem.errors import AuthError
from agentsystem.gateway.context import RequestContext, reset_context, set_context


def _ctx(bu: tuple[str, ...], regions: tuple[str, ...]) -> RequestContext:
    return RequestContext(
        user_id="u_test",
        session_id="s" * 32,
        trace_id="t-scope",
        bu_codes=frozenset(bu),
        regions=frozenset(regions),
    )


@pytest.fixture
def as_tile_sales():
    """瓷砖销售：BU-B + 仅华东。越权测试的主角。"""
    token = set_context(_ctx(("BU-B",), ("华东",)))
    yield
    reset_context(token)


def _sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def test_bu_filter_is_injected_without_being_asked(as_tile_sales) -> None:
    """🔴 调用方没提权限，WHERE 里也必须有。

    这是「注入在 db 层而非端点层」的全部意义：端点函数签名里根本没有
    bu_codes，想漏都漏不掉。
    """
    sql = _sql(scoped_select(text("*"), table="sales_order"))
    assert "bu_code IN ('BU-B')" in sql
    assert "region IN ('华东')" in sql


def test_requesting_out_of_scope_bu_raises_not_returns_empty(as_tile_sales) -> None:
    """🔴 越权必须显式报错，不得静默返回空。

    静默返回空会让用户以为「确实没有这批数据」，而真相是「你无权看」——
    这两件事在业务上完全不同，且后者是必须让用户知道的（宪法第十条）。
    """
    with pytest.raises(AuthError) as exc:
        scoped_select(text("*"), table="sales_order", bu=frozenset({"BU-A"}))
    assert exc.value.code == "AUTH_BU_OUT_OF_SCOPE"


def test_narrowing_within_scope_is_allowed(as_tile_sales) -> None:
    """允许集合内收窄：请求 BU-B（授权内）应当放行。"""
    sql = _sql(scoped_select(text("*"), table="sales_order", bu=frozenset({"BU-B"})))
    assert "bu_code IN ('BU-B')" in sql


def test_missing_context_fails_closed() -> None:
    """🔴 没有上下文时抛错，而不是"不过滤"。

    一条忘了设上下文的调用路径，若默认不过滤，就是静默的全量泄漏通道。
    """
    with pytest.raises(Exception) as exc:
        scoped_select(text("*"), table="sales_order")
    assert "context" in str(exc.value).lower() or "上下文" in str(exc.value)


def test_unregistered_table_raises(as_tile_sales) -> None:
    """新表忘了登记 scope 列，必须立刻炸。

    否则它的查询会少一层过滤，而且没有任何征兆。
    """
    with pytest.raises(ScopeError) as exc:
        scoped_select(text("*"), table="some_new_table")
    assert "未在 _SCOPE_COLUMNS 登记" in str(exc.value)


def test_order_line_must_go_through_order_header(as_tile_sales) -> None:
    """订单行没有自己的 bu/region，不可直接 scoped_select。

    显式拒绝而非默默不过滤 —— 后者会让「查订单行」成为一条绕过权限的路径。
    """
    with pytest.raises(ScopeError) as exc:
        scoped_select(text("*"), table="sales_order_line")
    assert "JOIN sales_order" in str(exc.value)


def test_region_filter_on_table_without_region_raises(as_tile_sales) -> None:
    """对无 region 列的表按区域过滤 —— 静默忽略会让筛选看起来生效了。"""
    with pytest.raises(ScopeError) as exc:
        scoped_select(text("*"), table="product", region=frozenset({"华东"}))
    assert "无 region 列" in str(exc.value)


def test_wildcard_region_means_unrestricted() -> None:
    """区域通配时不加区域条件 —— 但 BU 条件仍在。"""
    token = set_context(_ctx(("BU-B",), ("*",)))
    try:
        sql = _sql(scoped_select(text("*"), table="sales_order"))
        assert "bu_code IN ('BU-B')" in sql
        assert "region IN" not in sql
    finally:
        reset_context(token)


# ── 机械检查：业务表的查询只能经 scope 模块构造 ──────────────
_BUSINESS_MODELS = {
    "SalesOrder",
    "SalesOrderLine",
    "InventoryBatch",
    "ProductionPlan",
    "Product",
    "Color",
    "ProductionLine",
}
_EXEMPT = {"src/agentsystem/db/scope.py", "src/agentsystem/fixtures/loader.py"}


def test_business_tables_are_never_selected_directly() -> None:
    """🔴 业务模型不得出现在裸 select() 里。

    规则写在文档里靠自觉，写成检查才有效 —— 这条正是「漏一个就是越权」的
    机械保障。fixtures/loader 豁免：它灌的是种子数据，不经权限层。
    """
    offenders: list[str] = []
    for path in pathlib.Path("src/agentsystem").rglob("*.py"):
        rel = path.as_posix()
        if rel in _EXEMPT:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id != "select":
                continue
            for arg in node.args:
                name = arg.id if isinstance(arg, ast.Name) else getattr(arg, "attr", None)
                if name in _BUSINESS_MODELS:
                    offenders.append(f"{rel}:{node.lineno} select({name})")
    assert not offenders, "业务表须经 scoped_select 查询：\n  " + "\n  ".join(offenders)
