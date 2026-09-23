"""拼批判定的纯函数测试（库存复检，P2 详细设计 §3.2 步骤③）。

规则来源：``corpus/06-色差与等级判定标准.md`` §2.2。最要紧的是最后那组 ——
「两批各自对大样都在容差内，但彼此差值超出」，那正是把绝对 ΔE 当缸间差值的错法
会放过的情形（原文举的例子：0.8 与 0.9 都是优等品，缸间实际差值可能到 1.7）。
"""

from decimal import Decimal as D

import pytest

from agentsystem.orders.allocation import Candidate, pick_batches


def c(batch: str, qty: str, de: str | None = "0.5") -> Candidate:
    """造一个候选批次。"""
    return Candidate(batch, D(qty), D(de) if de is not None else None)


# ── SAME_BATCH ───────────────────────────────────────────────────


def test_same_batch_picks_one_batch_that_covers_the_line() -> None:
    """有单批够量就只用它。"""
    got = pick_batches(
        [c("K1", "50"), c("K2", "120")], qty=D(100), batch_policy="SAME_BATCH", tolerance=None
    )
    assert got == ["K2"]


def test_same_batch_refuses_to_combine_even_if_total_suffices() -> None:
    """合计够、单批不够 —— 必须拒绝。这是 SAME_BATCH 与 ANY 唯一的区别所在。"""
    got = pick_batches(
        [c("K1", "60"), c("K2", "60")], qty=D(100), batch_policy="SAME_BATCH", tolerance=None
    )
    assert got is None


def test_same_batch_with_no_candidates() -> None:
    """没有候选时拒绝，而不是抛异常。"""
    assert pick_batches([], qty=D(1), batch_policy="SAME_BATCH", tolerance=None) is None


# ── ANY ──────────────────────────────────────────────────────────


def test_any_combines_batches_preferring_large_ones() -> None:
    """ANY 可拼，且先用大批次 —— 批次越少，收货后分区越省事。"""
    got = pick_batches(
        [c("K1", "30"), c("K2", "80"), c("K3", "40")],
        qty=D(100),
        batch_policy="ANY",
        tolerance=None,
    )
    assert got == ["K2", "K3"]


def test_any_rejects_when_total_is_short() -> None:
    """合计都不够，拒绝。"""
    assert (
        pick_batches([c("K1", "30"), c("K2", "30")], qty=D(100), batch_policy="ANY", tolerance=None)
        is None
    )


def test_any_ignores_delta_e() -> None:
    """ANY 只要求各批单独合格，与色差无关 —— 连没测 ΔE 的批次都能用。"""
    got = pick_batches([c("K1", "100", de=None)], qty=D(100), batch_policy="ANY", tolerance=None)
    assert got == ["K1"]


# ── CROSS_OK_WITHIN_TOL ──────────────────────────────────────────


def test_cross_combines_batches_within_tolerance() -> None:
    """ΔE 1.0 与 1.8 差 0.8 ≤ 1.0，可拼。"""
    got = pick_batches(
        [c("D1", "60", "1.0"), c("D2", "60", "1.8")],
        qty=D(100),
        batch_policy="CROSS_OK_WITHIN_TOL",
        tolerance=D("1.0"),
    )
    assert sorted(got) == ["D1", "D2"]


def test_cross_rejects_pair_whose_difference_exceeds_tolerance() -> None:
    """🔴 两批各自对大样都很小（0.2 / 1.5），差值 1.3 > 1.0 —— 必须拒绝。

    拿单批 ΔE 与容差比的错法会放过它：0.2 与 1.5 各自都「看起来不大」。
    """
    got = pick_batches(
        [c("D1", "60", "0.2"), c("D2", "60", "1.5")],
        qty=D(100),
        batch_policy="CROSS_OK_WITHIN_TOL",
        tolerance=D("1.0"),
    )
    assert got is None


def test_cross_finds_a_compatible_cluster_among_outliers() -> None:
    """离群批次不该拖垮整组：{1.9, 2.3, 2.6} 彼此在 1.0 内，0.1 与 4.0 被排除。"""
    got = pick_batches(
        [
            c("D0", "500", "0.1"),
            c("D1", "40", "1.9"),
            c("D2", "40", "2.3"),
            c("D3", "40", "2.6"),
            c("D4", "500", "4.0"),
        ],
        qty=D(110),
        batch_policy="CROSS_OK_WITHIN_TOL",
        tolerance=D("1.0"),
    )
    assert got is not None
    # 所选批次中任意两批差值 ≤ 容差 —— 用定义本身核对，而不是核对具体选了谁。
    chosen = {"D0": D("0.1"), "D1": D("1.9"), "D2": D("2.3"), "D3": D("2.6"), "D4": D("4.0")}
    values = [chosen[b] for b in got]
    assert max(values) - min(values) <= D("1.0")


def test_cross_excludes_batches_without_delta_e() -> None:
    """没测 ΔE 的批次无法证明满足缸间容差，只能排除（失败关闭）。"""
    got = pick_batches(
        [c("D1", "60", "1.0"), c("D2", "60", None)],
        qty=D(100),
        batch_policy="CROSS_OK_WITHIN_TOL",
        tolerance=D("1.0"),
    )
    assert got is None


def test_cross_boundary_is_inclusive() -> None:
    """差值恰好等于容差时允许 —— 原文是「≤」。"""
    got = pick_batches(
        [c("D1", "60", "1.0"), c("D2", "60", "2.0")],
        qty=D(100),
        batch_policy="CROSS_OK_WITHIN_TOL",
        tolerance=D("1.0"),
    )
    assert got is not None


def test_cross_without_tolerance_is_a_contract_breach() -> None:
    """库表有 CHECK 保证两者同在；走到这里说明载荷绕过了约束，要炸。"""
    with pytest.raises(ValueError):
        pick_batches([c("D1", "100")], qty=D(1), batch_policy="CROSS_OK_WITHIN_TOL", tolerance=None)


def test_unknown_policy_is_rejected() -> None:
    """未知策略不能被当成 ANY 放行。"""
    with pytest.raises(ValueError):
        pick_batches([c("D1", "100")], qty=D(1), batch_policy="WHATEVER", tolerance=None)
