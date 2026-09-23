"""库存复检中的拼批判定（P2 详细设计 §3.2 步骤③）。

## 为什么是纯函数

拼批规则是本项目最容易「看起来对」的业务逻辑之一：``CROSS_OK_WITHIN_TOL``
比的是**缸间** ΔE 差值，不是各缸对大样的 ΔE 绝对值（QC-STD-006 §2.2），
而后者恰好是库里现成的那一列，顺手就会拿它去和容差比。写成纯函数，
三种策略的每条边界都能不连数据库逐一钉住。

## 规则来源与本期取舍

规则以 ``corpus/06-色差与等级判定标准.md`` §2.2 为准（知识库里被检索的那份，
权威高于其他文档里的转述）：

* ``SAME_BATCH``：整行须由同一批次供货，不足不得拼单。
* ``CROSS_OK_WITHIN_TOL``：可跨批，但**任意两批**之间 ΔE 差值 ≤ 容差，
  即所选批次的 ``max(ΔE) − min(ΔE) ≤ tol``。
* ``ANY``：任意跨批，各批单独满足等级即可。

本期**不做**的（均为保守方向，只会多拒不会多放）：

* 品级**严格相等**，不以高代低。优等品替一等品在工艺上可行，但价格不同，
  静默替换等于改了合同条款 —— 这该由人决定，不该由复检逻辑决定。
* 建陶的「色档」规则（§3.2）与深色系的阈值放宽（§2.1 例外）未建模。
  ``batch_policy`` 是跨事业部的通用字段，色档是 BU-B 专有语义，留给 P3 的
  知识库问答去解释，复检只执行通用字段的约束。
"""

from dataclasses import dataclass
from decimal import Decimal

#: 可发货的质检状态。**待检不可发**（边界-07 专门造了这类批次来考这一点），
#: 不合格与状态缺失同样不可发 —— 缺失即未知，未知按不可发处理（失败关闭）。
SHIPPABLE_QC = frozenset({"合格", "让步接收"})


@dataclass(frozen=True, slots=True)
class Candidate:
    """一个候选批次（已按 batch_no 汇总跨仓库存）。

    同一批次可分放在多个仓库，``inventory_batch`` 的唯一键里含仓库。
    「同一批次供货」指同一个 batch_no，与在几个仓库无关，所以要先汇总。
    """

    batch_no: str
    qty: Decimal
    #: 本批对标准大样的 ΔE。跨仓的同一批次理应同值；不同值时取最大，偏保守。
    delta_e: Decimal | None


def pick_batches(
    candidates: list[Candidate],
    *,
    qty: Decimal,
    batch_policy: str,
    tolerance: Decimal | None,
) -> list[str] | None:
    """判断候选批次能否满足一行订单，能则给出一组批次号。

    Args:
        candidates: 已按品级、质检状态、产品、色号过滤并按 batch_no 汇总的候选。
        qty: 订单行需求量。
        batch_policy: ``SAME_BATCH`` / ``CROSS_OK_WITHIN_TOL`` / ``ANY``。
        tolerance: 缸间 ΔE 容差，仅 ``CROSS_OK_WITHIN_TOL`` 使用。

    Returns:
        满足需求的批次号列表；满足不了返回 ``None``。

    Raises:
        ValueError: 未知的 batch_policy，或 CROSS_OK_WITHIN_TOL 缺容差。
            库表有 CHECK 约束保证两者不会出现，走到这里说明载荷被绕过了约束。
    """
    if batch_policy == "SAME_BATCH":
        # 取量最大的那一批：若它都不够，其余更不够。
        best = max(candidates, key=lambda c: c.qty, default=None)
        return [best.batch_no] if best is not None and best.qty >= qty else None

    if batch_policy == "ANY":
        if sum((c.qty for c in candidates), Decimal(0)) < qty:
            return None
        # 优先用大批次，少拆批 —— 批次越少，客户收货后分区铺贴/裁剪越省事。
        chosen, acc = [], Decimal(0)
        for c in sorted(candidates, key=lambda c: c.qty, reverse=True):
            chosen.append(c.batch_no)
            acc += c.qty
            if acc >= qty:
                return chosen
        return None  # pragma: no cover —— 上面的总量判断已排除

    if batch_policy == "CROSS_OK_WITHIN_TOL":
        if tolerance is None:
            raise ValueError("CROSS_OK_WITHIN_TOL 须有 delta_e_tolerance")
        # 没有 ΔE 的批次无法证明满足缸间容差，只能排除（失败关闭）。
        measured = sorted((c for c in candidates if c.delta_e is not None), key=lambda c: c.delta_e)
        # 滑动窗口：按 ΔE 排序后，「任意两批差值 ≤ tol」等价于「窗口首尾差 ≤ tol」。
        # 对每个右端点，把左端点推进到满足容差为止，窗口内总量够即可。
        left, acc = 0, Decimal(0)
        for right, c in enumerate(measured):
            acc += c.qty
            while c.delta_e - measured[left].delta_e > tolerance:
                acc -= measured[left].qty
                left += 1
            if acc >= qty:
                return [b.batch_no for b in measured[left : right + 1]]
        return None

    raise ValueError(f"未知的 batch_policy: {batch_policy!r}")
