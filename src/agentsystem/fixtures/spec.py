"""生成规模与词表 —— 与 [fixture 规格](../../../docs/eval/FIXTURE-SPEC.md) 对应。

单独成文件的理由：这些数字是**规格的一部分**（少品种多批次是硬约束），
散在生成逻辑里改起来没人察觉。
"""

#: 固定随机种子。不是为了「可复现」这么笼统 —— 是为了让评测结果**可归因**：
#: 某条用例失败时要能区分「模型不行」与「这次数据恰好刁钻」。种子一变，
#: 两者无从分辨。写死在代码里而非配置：能改的东西迟早会被改，而改了之后
#: 所有历史评测结论的可比性一起失效。
SEED = 20260910

#: 每 BU 的 SKU 与色号数。**少品种、多批次是硬约束**：100 条批次若按 SKU 铺开，
#: 绝大多数 (product, color) 组合只有 1 个批次，跨缸澄清演示直接没数据可演。
SKUS_PER_BU = 12
COLORS_PER_BU = 7
LINES_PER_BU = 5

#: 三个区域。必须 ≥3 —— users.yaml 里 u_tile_01 只有「华东」，若只有两个区域，
#: 「域外有数据」这个越权测试的前提会很脆弱。
REGIONS = ("华东", "华南", "华北")

BU_PROFILES = {
    "BU-A": {
        "name": "印染",
        "categories": ("坯布", "印花布"),
        "uom": "米",
        "line_types": ("染缸", "定型机"),
        "line_prefix": "D",
        "stages": ("前处理", "染色", "后整理"),
        "capacity": (800, 1600),
        "attr_fn": "yarn",
    },
    "BU-B": {
        "name": "建陶瓷砖",
        "categories": ("岩板", "瓷砖"),
        "uom": "平方米",
        "line_types": ("压机", "窑炉", "抛光线"),
        "line_prefix": "K",
        "stages": ("压机", "窑炉", "抛光", "分级"),
        "capacity": (200, 600),
        "attr_fn": "tile",
    },
    "BU-C": {
        "name": "卫浴洁具",
        "categories": ("坐便器", "面盆", "浴缸"),
        "uom": "件",
        "line_types": ("注浆线", "隧道窑"),
        "line_prefix": "F",
        "stages": ("成型", "施釉", "烧成", "检验"),
        "capacity": (30, 120),
        "attr_fn": "sanitary",
    },
}

GRADES = ("优等品", "一等品", "合格品")
QC_STATUSES = ("合格", "合格", "合格", "待检", "让步接收")  # 加权：多数合格
ORDER_STATUSES = ("待评审", "已确认", "生产中", "部分发货", "已完成")
ORDER_TYPES = ("经销", "工程", "出口", "样品")

#: 关键形态的最低条数。这些不是「大概生成这么多」，是**断言的阈值**
#: （见 assertions.py）—— 生成器必须显式构造，随机撒点命中不了。
MIN_CROSS_BATCH_GROUPS = 3  # ≥3 批次且 ΔE 差值 >1.0 的 (product,color) 组
MIN_SHORTAGE_LINES = 3  # 需求量 > 全部可用量之和的订单行
MIN_DELAYED_ORDERS = 8  # 末道工序 plan_end > required_date
MIN_ONTIME_ORDERS = 8  # 阴性对照 —— 只有延误样本时恒答「会延误」也满分
MIN_PER_STATUS = 5  # 每种订单状态
MIN_PENDING_QC = 10  # 待检批次
MIN_CONCESSION_QC = 5  # 让步接收
MIN_SIMILAR_NAME_PAIRS = 2  # 跨 BU 名称高度相似
MIN_MULTI_STAGE_ORDERS = 20  # 订单行关联 ≥2 条计划
MIN_LINKED_CHAINS = 5  # 订单行关联 ≥3 条连续工序，可推「延一道影响后两道」
MIN_PARTIAL_PLANS = 10  # 0 < qty_completed < planned_qty
MIN_DARK_COLORS = 3  # is_dark 且有对应批次
MIN_POLICY_ORDERS = 20  # policy_code 非空
