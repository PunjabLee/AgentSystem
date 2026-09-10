"""生成器的形态断言（P2.2.2）。

跑在**内存中的 Dataset** 上，不碰数据库 —— 这样 CI 能在没有种子数据的库上
照样验证生成器没被改坏。数据库层的完整校验由 ``make seed`` 负责。
"""

from agentsystem.fixtures import generate, spec


def test_generation_is_deterministic() -> None:
    """同一种子必须产出完全相同的数据。

    不是笼统的「可复现」——是评测结果**可归因**的前提：某条用例失败时要能
    区分「模型不行」与「这次数据恰好刁钻」。种子一变，两者无从分辨。
    """
    a, b = generate(), generate()
    assert [p["product_code"] for p in a.products] == [p["product_code"] for p in b.products]
    assert [x["batch_no"] for x in a.batches] == [x["batch_no"] for x in b.batches]
    assert [o["order_no"] for o in a.orders] == [o["order_no"] for o in b.orders]


def test_different_seed_gives_different_data() -> None:
    """阴性对照：换种子必须产出不同数据。

    没有这条，一个忽略种子、永远返回同一份数据的实现也能让上一条通过。
    """
    a, b = generate(), generate(seed=spec.SEED + 1)
    assert [x["qty_available"] for x in a.batches] != [x["qty_available"] for x in b.batches]


def test_few_skus_many_batches() -> None:
    """🔴 少品种多批次是硬约束。

    按 SKU 铺开会让绝大多数 (product, color) 组合只有 1 个批次，跨缸澄清演示
    直接没数据可演。这条守的是那个约束本身，而不是某个具体数字。
    """
    ds = generate()
    combos = {(b["product_code"], b["color_code"]) for b in ds.batches}
    assert len(ds.batches) / len(combos) >= 1.3, (
        f"平均每组合仅 {len(ds.batches) / len(combos):.2f} 个批次 —— 品种铺太开"
    )


def test_topological_order_is_encoded_in_the_dataclass() -> None:
    """拓扑序编码在 Dataset 的字段顺序里。

    不建外键（§4.0）后顺序错了不会报错，只留孤儿行。把顺序编码进数据结构，
    是为了让「顺序错了」变成一件需要显式改动 Dataset 的事，而不是某个调用点
    随手一改就能破坏的东西。
    """
    from dataclasses import fields

    from agentsystem.fixtures.generator import Dataset

    order = [f.name for f in fields(Dataset)]
    assert order.index("products") < order.index("order_lines")
    assert order.index("orders") < order.index("order_lines")
    assert order.index("order_lines") < order.index("plans")


def test_batch_policy_and_tolerance_are_paired() -> None:
    """生成的订单行必须满足 CHECK 约束，否则灌库时才发现。"""
    for ln in generate().order_lines:
        has_tol = ln["delta_e_tolerance"] is not None
        assert has_tol == (ln["batch_policy"] == "CROSS_OK_WITHIN_TOL"), ln


def test_uom_matches_product_default() -> None:
    """单位与主数据一致 —— ETA 公式隐含「单位一致」，数据库不校验它。"""
    ds = generate()
    uom = {p["product_code"]: p["default_uom"] for p in ds.products}
    for ln in ds.order_lines:
        assert ln["uom"] == uom[ln["product_code"]]
    for b in ds.batches:
        assert b["uom"] == uom[b["product_code"]]
