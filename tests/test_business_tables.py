"""业务七表的约束断言（P2.1.1–2.1.8）。

只测**编码了业务规则的约束** —— 那些一旦被移除，数据会静默变脏而不是报错的。
纯结构（列类型、非空）不测：`alembic upgrade` 本身就是它们的检查。

不建外键（设计文档 §4.0）意味着参照完整性由三项补偿手段兜住，其中生成器的
孤儿行断言在 P2.2.3。本文件覆盖的是数据库层**仍然承担**的那部分。
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from agentsystem.db.session import get_app_sessionmaker


@pytest.fixture
async def admin_db():
    """以 app_migrator 连库。

    序列校准（``setval``）需要序列的 UPDATE 权限，``app_rw`` 只有 USAGE ——
    这个边界是对的：重置序列是管理操作，运行时角色不该有。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from agentsystem.settings import get_settings

    engine = create_async_engine(
        get_settings().dsn("migrator", driver="asyncpg"), pool_size=1, max_overflow=0
    )
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@pytest.fixture
async def db():
    """业务连接。每个用例自带事务并在末尾回滚，互不污染。"""
    async with get_app_sessionmaker()() as session:
        yield session
        await session.rollback()


_LINE = text(
    "INSERT INTO sales_order_line "
    "(order_no, line_no, product_code, spec, color_code, grade_required, "
    " batch_policy, delta_e_tolerance, qty, uom) "
    "VALUES (:o, :n, 'P-X', 'spec', 'C-1', '一等品', :policy, :tol, 100, '平方米')"
)


async def test_tolerance_required_iff_cross_batch(db) -> None:
    """🔴 ΔE 容差与拼批策略必须配套。

    一个 `SAME_BATCH` 却带着容差值的行，读的人无从判断哪个才算数 —— 而拼批
    判定会因此在「必须同缸」和「可跨缸」之间摇摆。反向同理：声明可跨缸却不给
    容差，判定时无阈值可用，代码只能默默放行或默默拒绝，两种都是错的。
    """
    for policy, tol in [("SAME_BATCH", 1.5), ("CROSS_OK_WITHIN_TOL", None), ("ANY", 0.8)]:
        with pytest.raises(IntegrityError) as exc:
            await db.execute(_LINE, {"o": "SO-T", "n": 1, "policy": policy, "tol": tol})
        assert "tolerance_iff_cross" in str(exc.value)
        await db.rollback()


async def test_valid_policy_tolerance_pairs_are_accepted(db) -> None:
    """阳性对照：配套正确的组合必须能插入。

    没有这条，一个「拒绝一切」的约束也能让上一条通过。
    """
    for i, (policy, tol) in enumerate(
        [("SAME_BATCH", None), ("CROSS_OK_WITHIN_TOL", 1.5), ("ANY", None)]
    ):
        await db.execute(_LINE, {"o": "SO-OK", "n": i + 1, "policy": policy, "tol": tol})
    await db.rollback()


async def test_unknown_batch_policy_is_rejected(db) -> None:
    """策略枚举之外的取值不得入库 —— 否则拼批判定会撞上未知分支。"""
    with pytest.raises(IntegrityError) as exc:
        await db.execute(_LINE, {"o": "SO-T", "n": 1, "policy": "MAYBE", "tol": None})
    assert "batch_policy_enum" in str(exc.value)


async def test_plan_window_must_not_be_inverted(db) -> None:
    """计划结束不得早于开始。

    倒挂的时间窗会让 ETA 推演算出负工时，而延误判定是 `ETA > required_date`
    的大小比较 —— 负数不会报错，只会得出「不会延误」的错误结论。
    """
    with pytest.raises(IntegrityError) as exc:
        await db.execute(
            text(
                "INSERT INTO production_plan (plan_no, bu_code, line_code, product_code, "
                " color_code, process_stage, stage_seq, planned_qty, uom, "
                " plan_start, plan_end, status) "
                "VALUES ('PP-T','BU-B','K-03','P-X','C-1','窑炉',2,500,'平方米',"
                " '2026-10-02','2026-10-01','已排')"
            )
        )
    assert "plan_window" in str(exc.value)


async def test_inventory_qty_cannot_be_negative(db) -> None:
    """可用量不得为负。

    负库存在求和时会**抵消掉**其他批次的正数，「够不够发」直接得出错误答案，
    且不留任何痕迹。
    """
    with pytest.raises(IntegrityError) as exc:
        await db.execute(
            text(
                "INSERT INTO inventory_batch (warehouse_code, region, bu_code, "
                " product_code, batch_no, "
                " color_code, grade, spec, qty_available, uom) "
                "VALUES ('WH-1','华东','BU-B','P-X','B-1','C-1','一等品','spec',-1,'平方米')"
            )
        )
    assert "qty_nonneg" in str(exc.value)


async def test_batch_identity_is_unique(db) -> None:
    """同仓同产品同批同色同级只能有一行。

    缺它则同一批次可被拆成多行，库存求和虚高 —— 而这正是 S2 要答的问题。
    """
    ins = text(
        "INSERT INTO inventory_batch (warehouse_code, region, bu_code, product_code, batch_no, "
        " color_code, grade, spec, qty_available, uom) "
        "VALUES ('WH-U','华东','BU-B','P-X','B-U','C-1','一等品','spec',10,'平方米')"
    )
    await db.execute(ins)
    with pytest.raises(IntegrityError) as exc:
        await db.execute(ins)
    assert "uq_inventory_batch" in str(exc.value)


async def test_order_line_no_is_unique_within_order(db) -> None:
    """行号在订单内唯一 —— 否则「第 2 行」指向不确定。"""
    await db.execute(_LINE, {"o": "SO-DUP", "n": 1, "policy": "ANY", "tol": None})
    with pytest.raises(IntegrityError) as exc:
        await db.execute(_LINE, {"o": "SO-DUP", "n": 1, "policy": "ANY", "tol": None})
    assert "uq_sales_order_line" in str(exc.value)


async def test_confirm_token_is_unique(db) -> None:
    """🔴 幂等键唯一 —— 「含 interrupt 的节点会重放」的最后一道防线。

    移除本约束等同于移除二次确认的兜底（宪法一之附则）。
    """
    ins = text(
        "INSERT INTO sales_order (order_no, bu_code, region, customer_code, customer_name, "
        " order_type, order_date, required_date, status, confirm_token) "
        "VALUES (:o,'BU-B','华东','C-1','化名客户','经销',"
        " '2026-09-10','2026-10-10','待评审','tok-dup')"
    )
    await db.execute(ins, {"o": "SO-A"})
    with pytest.raises(IntegrityError) as exc:
        await db.execute(ins, {"o": "SO-B"})
    assert "confirm_token" in str(exc.value)


async def test_null_confirm_tokens_do_not_collide(db) -> None:
    """种子数据没有令牌 —— 多行 NULL 不得互相冲突。

    唯一约束在 PostgreSQL 里不约束 NULL，这条钉住该行为：否则「给历史数据
    补令牌」会成为建表的前置条件。
    """
    ins = text(
        "INSERT INTO sales_order (order_no, bu_code, region, customer_code, customer_name, "
        " order_type, order_date, required_date, status) "
        "VALUES (:o,'BU-B','华东','C-1','化名客户','经销','2026-09-10','2026-10-10','待评审')"
    )
    for o in ("SO-N1", "SO-N2", "SO-N3"):
        await db.execute(ins, {"o": o})
    await db.rollback()


async def test_region_is_required_on_every_filtered_table(db) -> None:
    """🔴 三条查询路径的过滤锚点都必须有 region，且非空。

    users.yaml 早就给用户配了 `regions`，RequestContext.narrow_region() 也在，
    但业务表此前**一个 region 列都没有** —— 权限模型缺了一半，区域级越权
    无从表达。可空同样不行：NULL 在 `region = ANY(:allowed)` 下既不匹配也不
    报错，等于把那一行对所有人隐藏，或（若写成 NOT IN）对所有人暴露。
    """
    rows = (
        await db.execute(
            text(
                "SELECT table_name, is_nullable FROM information_schema.columns "
                " WHERE column_name = 'region' AND table_schema = 'public'"
            )
        )
    ).all()
    got = {r.table_name: r.is_nullable for r in rows}
    assert set(got) == {"sales_order", "inventory_batch", "production_line"}, (
        f"region 覆盖的表不对: {sorted(got)}"
    )
    assert all(v == "NO" for v in got.values()), f"region 不得可空: {got}"


async def test_resync_sequences_moves_past_explicit_ids(admin_db) -> None:
    """🔴 显式插入主键后必须校准序列，否则下次自增插入撞主键。

    生成器显式赋 `line_id`（`production_plan.related_order_line` 要引用它，
    两表分开插入拿不到自增值），而**显式插入不会推进序列**。灌完 200 多行后
    序列仍停在个位数。

    报错形态很有迷惑性 —— 报 `duplicate key`，指向「有人插了重复数据」，
    与真正的原因（序列没跟上）差得很远。实测踩到：灌完数据后，一条本该报
    唯一约束冲突的用例改报了主键冲突。P2.3.4 的写端点会撞上同一件事。

    本用例自带数据，不依赖库里已灌过种子 —— 依赖环境状态的用例在干净的 CI 上
    只会被跳过，而 skip 不是 pass。
    """
    from agentsystem.fixtures.loader import resync_sequences

    high = 900_001
    await admin_db.execute(
        text(
            "INSERT INTO sales_order_line (line_id, order_no, line_no, product_code, "
            " spec, color_code, grade_required, batch_policy, qty, uom) "
            "VALUES (:id, 'SO-SEQ', 1, 'P-X', 'spec', 'C-1', '一等品', 'ANY', 1, '件')"
        ),
        {"id": high},
    )

    after = (await resync_sequences(admin_db))["sales_order_line_line_id_seq"]
    assert after > high, f"序列 {after} 未越过显式插入的 {high}"

    # 阳性对照：校准之后自增插入必须能成功，且不撞已有行
    new_id = (
        await admin_db.execute(
            text(
                "INSERT INTO sales_order_line (order_no, line_no, product_code, spec, "
                " color_code, grade_required, batch_policy, qty, uom) "
                "VALUES ('SO-SEQ', 2, 'P-X', 'spec', 'C-1', '一等品', 'ANY', 1, '件') "
                "RETURNING line_id"
            )
        )
    ).scalar_one()
    assert new_id > high
    await admin_db.rollback()


async def test_runtime_role_cannot_reset_sequences(db) -> None:
    """🔴 运行时角色不得重置序列 —— 只能 nextval，不能 setval。

    权限边界：`app_rw` 有 USAGE（自增插入需要），没有 UPDATE（`setval` 需要）。
    能重置序列意味着能让后续插入撞上已有主键，是一种可以静默制造数据损坏的
    能力，运行时账号不该有。

    这条也是灌数据必须走 `app_migrator` 的原因（见 scripts/seed.py）。
    """
    from sqlalchemy.exc import ProgrammingError

    with pytest.raises(ProgrammingError) as exc:
        await db.execute(text("SELECT setval('sales_order_line_line_id_seq', 1, false)"))
    assert "permission denied" in str(exc.value).lower()
