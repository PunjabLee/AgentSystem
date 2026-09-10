"""生成后断言校验（P2.2.3）。

## 为什么这一项非可选

P0-2 造数据时 ``bu`` 与 ``year`` 都用 ``i % 2``，两维完全相关，四维过滤查询
**直接返回 0 行**。生成器当时"跑通了"，问题到用例跑不出结果才暴露。

本模块与 [fixture 规格](../../../docs/eval/FIXTURE-SPEC.md) 的表格**一一对应**：
规格里每一行的「断言」列，这里都有一个检查函数。规格与检查不对应，规格就只是
一份没人验证的愿望清单。

## 两类检查

**关键形态存在性**：某些用例没有对应形态的数据就跑不了（跨缸澄清需要
≥3 批次且 ΔE 差值 >1.0）。均匀随机产不出这些，必须显式构造 + 断言兜底。

**参照完整性**：不建外键（设计文档 §4.0）之后数据库不再拦孤儿行，这里是
§4.0 决策成立的三项前提之一。其中 ``bu_code`` 与主数据一致这条最容易漏 ——
**只查「code 存在」查不出它**，code 是存在的，只是属于别的 BU。
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agentsystem.fixtures import spec


@dataclass(frozen=True)
class CheckResult:
    """一条检查的结果。"""

    name: str
    passed: bool
    detail: str

    def describe(self) -> str:
        """给终端看的一行。"""
        return f"{'✅' if self.passed else '❌'} {self.name}  {self.detail}"


async def _scalar(db: AsyncSession, sql: str) -> int:
    return int((await db.execute(text(sql))).scalar_one())


# ── 关键形态 ────────────────────────────────────────────────
_SHAPE_CHECKS: list[tuple[str, str, int]] = [
    (
        "S2-03 跨缸澄清：≥3 批次且 ΔE 极差 >1.0 的组",
        "SELECT count(*) FROM (SELECT product_code, color_code FROM inventory_batch "
        " GROUP BY 1,2 HAVING count(*) >= 3 AND max(delta_e) - min(delta_e) > 1.0) t",
        spec.MIN_CROSS_BATCH_GROUPS,
    ),
    (
        "S3-05 库存不足：需求量 > 该组合全部可用量",
        "SELECT count(*) FROM sales_order_line l WHERE l.qty > "
        "COALESCE("
        " (SELECT sum(b.qty_available) FROM inventory_batch b"
        "   WHERE b.product_code = l.product_code AND b.color_code = l.color_code), 0)",
        spec.MIN_SHORTAGE_LINES,
    ),
    (
        "S5-02 真延误：末道工序 plan_end > required_date",
        "SELECT count(DISTINCT o.order_no) FROM sales_order o"
        " JOIN sales_order_line l ON l.order_no = o.order_no"
        " JOIN production_plan p ON p.related_order_line = l.line_id"
        " WHERE p.stage_seq = (SELECT max(p2.stage_seq) FROM production_plan p2"
        "                       WHERE p2.related_order_line = l.line_id)"
        "   AND p.plan_end::date > o.required_date",
        spec.MIN_DELAYED_ORDERS,
    ),
    (
        "S5-04 阴性对照：末道工序按期（否则恒答「会延误」也满分）",
        "SELECT count(DISTINCT o.order_no) FROM sales_order o"
        " JOIN sales_order_line l ON l.order_no = o.order_no"
        " JOIN production_plan p ON p.related_order_line = l.line_id"
        " WHERE p.stage_seq = (SELECT max(p2.stage_seq) FROM production_plan p2"
        "                       WHERE p2.related_order_line = l.line_id)"
        "   AND p.plan_end::date <= o.required_date",
        spec.MIN_ONTIME_ORDERS,
    ),
    (
        "S5-06 联动分析：订单行关联 ≥3 条连续工序",
        "SELECT count(*) FROM (SELECT related_order_line FROM production_plan"
        " WHERE related_order_line IS NOT NULL GROUP BY 1 HAVING count(*) >= 3) t",
        spec.MIN_LINKED_CHAINS,
    ),
    (
        "S5-01 多工序：订单行关联 ≥2 条计划",
        "SELECT count(DISTINCT l.order_no) FROM sales_order_line l"
        " WHERE (SELECT count(*) FROM production_plan p"
        "         WHERE p.related_order_line = l.line_id) >= 2",
        spec.MIN_MULTI_STAGE_ORDERS,
    ),
    (
        "S4-04 部分完成：0 < qty_completed < planned_qty",
        "SELECT count(*) FROM production_plan"
        " WHERE qty_completed > 0 AND qty_completed < planned_qty",
        spec.MIN_PARTIAL_PLANS,
    ),
    (
        "S1 交叉提问：policy_code 非空的订单",
        "SELECT count(*) FROM sales_order WHERE policy_code IS NOT NULL",
        spec.MIN_POLICY_ORDERS,
    ),
    (
        "边界-07 待检批次",
        "SELECT count(*) FROM inventory_batch WHERE qc_status = '待检'",
        spec.MIN_PENDING_QC,
    ),
    (
        "边界-08 让步接收批次",
        "SELECT count(*) FROM inventory_batch WHERE qc_status = '让步接收'",
        spec.MIN_CONCESSION_QC,
    ),
    (
        "边界-02 跨 BU 同名产品",
        "SELECT count(*) FROM (SELECT product_name FROM product"
        " GROUP BY 1 HAVING count(DISTINCT bu_code) >= 2) t",
        spec.MIN_SIMILAR_NAME_PAIRS,
    ),
    (
        "边界-10 深色系色号且有对应批次",
        "SELECT count(DISTINCT c.color_code) FROM color c"
        " JOIN inventory_batch b ON b.color_code = c.color_code AND b.bu_code = c.bu_code"
        " WHERE c.is_dark",
        spec.MIN_DARK_COLORS,
    ),
]

# ── 参照完整性（不建外键的补偿，§4.0）────────────────────────
_ORPHAN_CHECKS: list[tuple[str, str]] = [
    (
        "订单行 → 订单头",
        "SELECT count(*) FROM sales_order_line l"
        " WHERE NOT EXISTS (SELECT 1 FROM sales_order o WHERE o.order_no = l.order_no)",
    ),
    (
        "计划 → 订单行",
        "SELECT count(*) FROM production_plan p WHERE p.related_order_line IS NOT NULL"
        "  AND NOT EXISTS (SELECT 1 FROM sales_order_line l"
        "                   WHERE l.line_id = p.related_order_line)",
    ),
    (
        "订单行 → 产品",
        "SELECT count(*) FROM sales_order_line l"
        " WHERE NOT EXISTS (SELECT 1 FROM product p WHERE p.product_code = l.product_code)",
    ),
    (
        "库存 → 产品",
        "SELECT count(*) FROM inventory_batch b"
        " WHERE NOT EXISTS (SELECT 1 FROM product p WHERE p.product_code = b.product_code)",
    ),
    (
        "计划 → 产线",
        "SELECT count(*) FROM production_plan p"
        " WHERE NOT EXISTS (SELECT 1 FROM production_line l WHERE l.line_code = p.line_code)",
    ),
    (
        "库存 → 色号 (bu, color)",
        "SELECT count(*) FROM inventory_batch b WHERE NOT EXISTS"
        " (SELECT 1 FROM color c WHERE c.bu_code = b.bu_code AND c.color_code = b.color_code)",
    ),
    # 🔴 最容易漏的一条：code 存在但属于别的 BU。只查「code 存在」查不出它。
    (
        "库存 bu_code 与产品一致",
        "SELECT count(*) FROM inventory_batch b JOIN product p ON p.product_code = b.product_code"
        " WHERE b.bu_code <> p.bu_code",
    ),
    (
        "订单行 bu_code 与产品一致",
        "SELECT count(*) FROM sales_order_line l JOIN sales_order o ON o.order_no = l.order_no"
        " JOIN product p ON p.product_code = l.product_code WHERE o.bu_code <> p.bu_code",
    ),
    # 单位一致性：ETA 公式隐含「单位一致」，数据库不校验
    (
        "订单行 uom 与产品一致",
        "SELECT count(*) FROM sales_order_line l JOIN product p ON p.product_code = l.product_code"
        " WHERE l.uom <> p.default_uom",
    ),
    (
        "库存 uom 与产品一致",
        "SELECT count(*) FROM inventory_batch b JOIN product p ON p.product_code = b.product_code"
        " WHERE b.uom <> p.default_uom",
    ),
    (
        "计划 uom 与产线产能单位可比",
        "SELECT count(*) FROM production_plan p JOIN production_line l ON l.line_code = p.line_code"
        " WHERE l.capacity_uom <> p.uom || '/小时'",
    ),
]


async def run_all_checks(db: AsyncSession) -> list[CheckResult]:
    """跑完全部检查，返回逐条结果。

    不在第一条失败时就中断 —— 一次看全所有问题，比修一条跑一次快得多。

    Args:
        db: 业务库连接。

    Returns:
        逐条结果。``all(r.passed for r in ...)`` 为真才算数据可用。
    """
    results: list[CheckResult] = []

    for name, sql, floor in _SHAPE_CHECKS:
        got = await _scalar(db, sql)
        results.append(CheckResult(name, got >= floor, f"{got} 条（要求 ≥{floor}）"))

    for name, sql in _ORPHAN_CHECKS:
        got = await _scalar(db, sql)
        results.append(CheckResult(f"完整性 · {name}", got == 0, f"{got} 条异常（要求 = 0）"))

    # 越权测试的前提：每个受限用户的**域外**都要有数据。
    # ⚠️ 方向容易写反 —— 要断言的是「域外有数据」，不是「域外没有」。
    #    域外本来就没数据的话，模型什么都不做也能"通过"越权测试。
    for table, bu, region in (("sales_order", "BU-B", "华东"), ("inventory_batch", "BU-B", "华东")):
        got = await _scalar(
            db, f"SELECT count(*) FROM {table} WHERE bu_code <> '{bu}' OR region <> '{region}'"
        )
        results.append(
            CheckResult(
                f"边界-09 越权前提 · {table} 存在 u_tile_01 域外数据",
                got > 0,
                f"{got} 条域外（要求 >0）",
            )
        )

    # 每种订单状态都要有 —— 缺一种，对应的 S4 用例就跑不了
    for st in spec.ORDER_STATUSES:
        got = await _scalar(db, f"SELECT count(*) FROM sales_order WHERE status = '{st}'")
        results.append(
            CheckResult(
                f"S4 订单状态 · {st}",
                got >= spec.MIN_PER_STATUS,
                f"{got} 单（要求 ≥{spec.MIN_PER_STATUS}）",
            )
        )

    return results
