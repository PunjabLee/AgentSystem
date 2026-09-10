"""模拟数据生成器（P2.2.2）。

## 两条不可违反的规则

**① 按拓扑序生成**：主数据 → 订单头 → 订单行 → 计划。不建外键（设计文档
§4.0）之后数据库不再拦孤儿行，顺序错了不会报错，只会在查询时静默少返回。
这是 §4.0 决策成立的三项前提之一。

**② 关键形态显式构造，不靠随机命中**。P0-2 造数据时 ``bu`` 与 ``year`` 都用
``i % 2``，两维完全相关，四维过滤查询直接返回 0 行 —— 这不是理论风险，是本项目
发生过的事故。

不过「随机产不出关键形态」这句话要分形态而论，实测（2026-09-10）：

* **完全依赖显式构造**：跨 BU 同名产品（恰好 2 条 = 阈值）、深色系色号
  （恰好 3 条 = 阈值）。条件太窄，随机撒点命中率约等于零。
* **随机填充有助力**：跨缸澄清组（9 条 > 阈值 3）、真延误订单（26 > 8）。
  把显式构造去掉，随机填充仍能凑够 —— 试过，断言不转红。

所以显式构造对后一类是**保险而非唯一来源**：换个种子、换个数据量，运气就
未必还在。断言的阈值守的正是这一点。

生成完必须跑 :mod:`agentsystem.fixtures.assertions`。生成器写对了但数据不对，
只有断言查得出来。
"""

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from agentsystem.fixtures import spec


@dataclass
class Dataset:
    """一次生成的全部行。按表分组，写库时按本类字段的**声明顺序**插入。

    顺序即拓扑序 —— 把它编码进字段顺序而不是散在调用点，是为了让「顺序错了」
    变成一件需要显式改动本类的事。
    """

    products: list[dict] = field(default_factory=list)
    colors: list[dict] = field(default_factory=list)
    lines: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    order_lines: list[dict] = field(default_factory=list)
    batches: list[dict] = field(default_factory=list)
    plans: list[dict] = field(default_factory=list)


def _attrs(kind: str, rng: random.Random) -> dict:
    """按 BU 生成结构化属性。三个 BU 的属性真实发散，故不共用 schema。"""
    if kind == "yarn":
        return {
            "width_cm": rng.choice([150, 160, 180, 220]),
            "gsm": rng.choice([120, 150, 180, 240]),
            "yarn_count": rng.choice(["32S", "40S", "60S"]),
        }
    if kind == "tile":
        return {
            "edge_mm": rng.choice([600, 750, 900, 1200]),
            "thick_mm": rng.choice([6, 9, 12, 15]),
            "surface": rng.choice(["哑光", "亮光", "柔光", "岩面"]),
        }
    return {
        "glaze": rng.choice(["白釉", "米釉", "灰釉"]),
        "water_use_l": rng.choice([3.5, 4.0, 4.5, 5.0]),
    }


def _master_data(ds: Dataset, rng: random.Random) -> None:
    """第一层：主数据。其余各表都引用它，必须最先生成。"""
    tile_names = ["岩板 900×1800 素色系列", "通体大理石瓷砖 750×1500"]

    for bu, prof in spec.BU_PROFILES.items():
        for i in range(spec.SKUS_PER_BU):
            cat = prof["categories"][i % len(prof["categories"])]
            name = f"{cat} {prof['name']}系列-{i + 1:02d}"
            ds.products.append(
                {
                    "product_code": f"P-{bu[-1]}-{1001 + i}",
                    "bu_code": bu,
                    "product_name": name,
                    "category": cat,
                    "spec": f"SPEC-{bu[-1]}{i + 1:02d}",
                    "attrs": _attrs(prof["attr_fn"], rng),
                    "default_uom": prof["uom"],
                    "status": "active",
                }
            )

        # 色号。前 spec.MIN_DARK_COLORS 个显式设为深色系 —— 深色系例外规则
        # （QC-STD-006 §2.1：优等品 ΔE 上限由 1.0 放宽到 1.5）是边界-10 的
        # 唯一数据依托，随机生成可能一个都没有。
        families = ["白色系", "深色系", "彩色系"]
        for i in range(spec.COLORS_PER_BU):
            is_dark = i < spec.MIN_DARK_COLORS
            ds.colors.append(
                {
                    "bu_code": bu,
                    "color_code": f"C-{i + 1:03d}",  # 刻意跨 BU 同码：边界-04 的依托
                    "color_name": ["藏青", "墨绿", "深灰", "米白", "浅杏", "天青", "砖红"][i],
                    "color_family": "深色系" if is_dark else families[i % 3],
                    "is_dark": is_dark,
                    "std_sample_ref": f"STD-{bu[-1]}-{i + 1:03d}",
                }
            )

        for i in range(spec.LINES_PER_BU):
            lt = prof["line_types"][i % len(prof["line_types"])]
            lo, hi = prof["capacity"]
            ds.lines.append(
                {
                    "line_code": f"{prof['line_prefix']}-{i + 1:02d}-{bu[-1]}",
                    "bu_code": bu,
                    "line_name": f"{lt}{i + 1}号",
                    "line_type": lt,
                    "capacity_per_hour": round(rng.uniform(lo, hi), 3),
                    "capacity_uom": f"{prof['uom']}/小时",
                    # 区域轮转而非随机：保证每个区域都有产线，且分布确定。
                    "region": spec.REGIONS[i % len(spec.REGIONS)],
                    "status": "running" if i % 5 else "maintenance",
                }
            )

    # 边界-02 跨 BU 同名：显式插入名称高度相似的一对，随机生成撞不出来。
    for j, nm in enumerate(tile_names[: spec.MIN_SIMILAR_NAME_PAIRS]):
        ds.products.append(
            {
                "product_code": f"P-A-90{j + 1}",
                "bu_code": "BU-A",  # 名字像瓷砖，实际挂在印染 BU 下
                "product_name": nm,
                "category": "印花布",
                "spec": f"SPEC-X{j + 1}",
                "attrs": _attrs("yarn", rng),
                "default_uom": spec.BU_PROFILES["BU-A"]["uom"],
                "status": "active",
            }
        )
        ds.products.append(
            {
                "product_code": f"P-B-90{j + 1}",
                "bu_code": "BU-B",
                "product_name": nm,  # 与上一条同名，分属不同 BU
                "category": "岩板",
                "spec": f"SPEC-Y{j + 1}",
                "attrs": _attrs("tile", rng),
                "default_uom": spec.BU_PROFILES["BU-B"]["uom"],
                "status": "active",
            }
        )


def _inventory(ds: Dataset, rng: random.Random) -> None:
    """第二层：库存批次。

    先显式造出「跨缸澄清」需要的多批次组，再补齐到目标条数 —— 顺序不能反：
    先随机铺满再指望其中恰好有 ≥3 批次且 ΔE 差值 >1.0 的组，是碰运气。
    """
    by_bu: dict[str, list[dict]] = {}
    for p in ds.products:
        by_bu.setdefault(p["bu_code"], []).append(p)
    colors_by_bu: dict[str, list[dict]] = {}
    for c in ds.colors:
        colors_by_bu.setdefault(c["bu_code"], []).append(c)

    seq = 0

    def add(prod: dict, color: dict, *, delta_e: float, qty: float, qc: str, region: str) -> None:
        nonlocal seq
        seq += 1
        ds.batches.append(
            {
                "warehouse_code": f"WH-{region[0]}{prod['bu_code'][-1]}",
                "region": region,
                "bu_code": prod["bu_code"],  # 与 product 一致 —— §4.1.1 点名的脏数据形态
                "product_code": prod["product_code"],
                "batch_no": f"B{seq:05d}",
                "color_code": color["color_code"],
                "grade": spec.GRADES[seq % len(spec.GRADES)],
                "spec": prod["spec"],
                "delta_e": round(delta_e, 2),
                "qty_available": round(qty, 3),
                "qty_locked": 0,
                "uom": prod["default_uom"],  # 与 product.default_uom 一致
                "inbound_date": date(2026, 1 + seq % 8, 1 + seq % 27),
                "qc_status": qc,
            }
        )

    # ① 跨缸澄清组：同一 (product, color) 三个批次，ΔE 跨度显式拉开到 >1.0。
    for bu in spec.BU_PROFILES:
        for k in range(spec.MIN_CROSS_BATCH_GROUPS):
            prod = by_bu[bu][k]
            color = colors_by_bu[bu][k]
            for d in (0.3, 1.1, 1.9):  # 极差 1.6 > 1.0
                add(
                    prod,
                    color,
                    delta_e=d,
                    qty=rng.uniform(200, 900),
                    qc="合格",
                    region=spec.REGIONS[k % 3],
                )

    # ② QC 状态：待检与让步接收按阈值显式铺够，其余随机。
    forced = ["待检"] * spec.MIN_PENDING_QC + ["让步接收"] * spec.MIN_CONCESSION_QC
    for i, qc in enumerate(forced):
        bu = list(spec.BU_PROFILES)[i % 3]
        add(
            by_bu[bu][i % spec.SKUS_PER_BU],
            colors_by_bu[bu][i % spec.COLORS_PER_BU],
            delta_e=rng.uniform(0.2, 2.5),
            qty=rng.uniform(50, 400),
            qc=qc,
            region=spec.REGIONS[i % 3],
        )

    # ③ 补齐到 ≥100 条。区域用 (i % 3) 轮转而非随机 —— P0-2 的教训是两维
    #    取同一个 i%n 会完全相关，这里 bu 用 i%3、region 用 (i//3)%3 错开。
    while len(ds.batches) < 110:
        i = len(ds.batches)
        bu = list(spec.BU_PROFILES)[i % 3]
        add(
            by_bu[bu][i % spec.SKUS_PER_BU],
            colors_by_bu[bu][(i // 2) % spec.COLORS_PER_BU],
            delta_e=rng.uniform(0.1, 3.0),
            qty=rng.uniform(20, 1200),
            qc=rng.choice(spec.QC_STATUSES),
            region=spec.REGIONS[(i // 3) % 3],
        )


def _orders_and_plans(ds: Dataset, rng: random.Random) -> None:
    """第三、四层：订单头 → 订单行 → 排产计划。

    延误与准时两类**都要显式构造**：只有延误样本时，一个恒答「会延误」的模型
    也能拿满分。阴性对照是判据可证伪的前提。
    """
    by_bu: dict[str, list[dict]] = {}
    for p in ds.products:
        by_bu.setdefault(p["bu_code"], []).append(p)
    colors_by_bu: dict[str, list[dict]] = {}
    for c in ds.colors:
        colors_by_bu.setdefault(c["bu_code"], []).append(c)
    lines_by_bu: dict[str, list[dict]] = {}
    for ln in ds.lines:
        lines_by_bu.setdefault(ln["bu_code"], []).append(ln)

    base = date(2026, 9, 1)
    line_id = 0
    plan_seq = 0

    def make_order(i: int, *, delayed: bool, status: str, n_lines: int, stages: int) -> None:
        """造一单，连同它的订单行与各行的多工序计划。"""
        nonlocal line_id, plan_seq
        bu = list(spec.BU_PROFILES)[i % 3]
        prof = spec.BU_PROFILES[bu]
        region = spec.REGIONS[(i // 3) % 3]
        order_no = f"SO-2026-{i + 1:06d}"
        required = base + timedelta(days=20 + i % 40)

        ds.orders.append(
            {
                "order_no": order_no,
                "bu_code": bu,
                "region": region,
                "customer_code": f"CUST-{bu[-1]}{i % 25:03d}",
                # 化名（宪法第八条）—— 固定词表拼装，不引入任何真实客户源数据
                "customer_name": f"{region}建材-{chr(65 + i % 26)}{i % 100:03d}",
                "order_type": spec.ORDER_TYPES[i % 4],
                "order_date": base + timedelta(days=i % 30),
                "required_date": required,
                "promised_date": required,
                "status": status,
                "total_amount": round(rng.uniform(5_000, 400_000), 2),
                # policy_code 按阈值铺够，供 S1↔S4 交叉提问
                "policy_code": f"POL-2026-{i % 6 + 1:02d}"
                if i < spec.MIN_POLICY_ORDERS + 10
                else None,
                "created_by": "seed",
                "confirm_token": None,  # 种子数据不经二次确认，故无令牌
            }
        )

        for ln in range(n_lines):
            line_id += 1
            prod = by_bu[bu][(i + ln) % len(by_bu[bu])]
            color = colors_by_bu[bu][(i + ln) % spec.COLORS_PER_BU]
            # 拼批策略轮转，并保证容差与策略配套（CHECK 约束会拦不配套的）
            policy = ("SAME_BATCH", "CROSS_OK_WITHIN_TOL", "ANY")[(i + ln) % 3]
            ds.order_lines.append(
                {
                    "line_id": line_id,
                    "order_no": order_no,
                    "line_no": ln + 1,
                    "product_code": prod["product_code"],
                    "spec": prod["spec"],
                    "color_code": color["color_code"],
                    "grade_required": spec.GRADES[(i + ln) % 3],
                    "batch_policy": policy,
                    "delta_e_tolerance": 1.5 if policy == "CROSS_OK_WITHIN_TOL" else None,
                    "qty": round(rng.uniform(100, 2000), 3),
                    "uom": prod["default_uom"],  # 与 product.default_uom 一致
                    "unit_price": round(rng.uniform(8, 260), 4),
                    "line_status": status,
                }
            )

            # 多工序计划。末道工序的 plan_end 决定延误与否 —— 显式控制它，
            # 而不是随机生成再指望其中有延误样本。
            lines_pool = lines_by_bu[bu]
            for st in range(stages):
                plan_seq += 1
                pline = lines_pool[(i + ln + st) % len(lines_pool)]
                is_last = st == stages - 1
                end_off = (
                    (required - base).days + 5
                    if (is_last and delayed)
                    else (required - base).days - 5 - (stages - st)
                )
                start = base + timedelta(days=max(1, end_off - 3))
                end = base + timedelta(days=end_off)
                planned = round(rng.uniform(100, 1500), 3)
                # 部分完成的形态：S4-04 依赖它
                done = (
                    round(planned * 0.4, 3)
                    if (i % 7 == 0 and st == 0)
                    else (planned if status == "已完成" else 0)
                )
                ds.plans.append(
                    {
                        "plan_no": f"PP-{plan_seq:06d}",
                        "bu_code": bu,
                        "line_code": pline["line_code"],
                        "product_code": prod["product_code"],
                        "color_code": color["color_code"],
                        "process_stage": prof["stages"][st % len(prof["stages"])],
                        "stage_seq": st + 1,
                        "planned_qty": planned,
                        "qty_completed": done,
                        "uom": prod["default_uom"],
                        "plan_start": datetime.combine(start, datetime.min.time()),
                        "plan_end": datetime.combine(end, datetime.min.time()),
                        "actual_start": None,
                        "actual_end": None,
                        "changeover_min": rng.choice([0, 30, 45, 90]),
                        "status": "已完成" if done == planned and planned > 0 else "已排",
                        "related_order_line": line_id,
                    }
                )

    i = 0
    # 每种订单状态各铺够阈值
    for status in spec.ORDER_STATUSES:
        for _ in range(spec.MIN_PER_STATUS + 1):
            make_order(i, delayed=False, status=status, n_lines=1 + i % 3, stages=2)
            i += 1
    # 延误 / 准时 各铺够（准时是阴性对照，不可省）
    for _ in range(spec.MIN_DELAYED_ORDERS + 2):
        make_order(i, delayed=True, status="生产中", n_lines=2, stages=3)
        i += 1
    for _ in range(spec.MIN_ONTIME_ORDERS + 2):
        make_order(i, delayed=False, status="生产中", n_lines=2, stages=3)
        i += 1
    # 三工序连续链：S5-06 联动分析的依托
    for _ in range(spec.MIN_LINKED_CHAINS + 2):
        make_order(i, delayed=True, status="生产中", n_lines=1, stages=3)
        i += 1
    # 补齐到 ≥100 单
    while len(ds.orders) < 105:
        make_order(
            i, delayed=i % 5 == 0, status=spec.ORDER_STATUSES[i % 5], n_lines=1 + i % 3, stages=2
        )
        i += 1


def generate(seed: int = spec.SEED) -> Dataset:
    """生成一整套模拟数据。

    Args:
        seed: 随机种子。默认用固定值 —— 见 ``spec.SEED`` 的说明。

    Returns:
        按拓扑序排列的数据集。**写库前必须跑 assertions.run_all_checks** ——
        生成器写对了但数据不对，只有断言查得出来。
    """
    rng = random.Random(seed)
    ds = Dataset()
    _master_data(ds, rng)  # 第一层：其余各表都引用它
    _inventory(ds, rng)  # 第二层：依赖 product / color
    _orders_and_plans(ds, rng)  # 第三、四层：依赖主数据，plans 依赖 order_lines
    return ds
