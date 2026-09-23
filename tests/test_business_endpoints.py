"""业务端点的集成测试（P2.3.4）。

测的是**性质**而不是样例：权限收窄在全量数据上成立、延误判定对每一张订单都与
冻结的评分细则一致、联动范围对每一道工序都等于细则定义的集合。样例测试只能
证明「这一张单对了」，而评分是在全量上做的。
"""

import ast
import os
import pathlib
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from agentsystem.api.app import create_app
from agentsystem.db.session import app_session
from agentsystem.gateway.identity import derive_session_id, get_identity_registry
from agentsystem.intent.repository import mint_intent


@dataclass
class Caller:
    """一个已领取会话的调用方。"""

    http: AsyncClient
    user_id: str
    session_id: str
    bu_codes: frozenset[str]
    regions: frozenset[str]


@asynccontextmanager
async def _as(env: str) -> AsyncIterator[Caller]:
    """以某个演示用户的身份建客户端。"""
    token = os.environ.get(env)
    if not token:
        pytest.skip(f"{env} 未配置")
    user = get_identity_registry().resolve(token)
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://t", timeout=60
    ) as c:
        cid = (await c.post("/v1/sessions", headers={"Authorization": f"Bearer {token}"})).json()[
            "conversation_id"
        ]
        c.headers.update({"Authorization": f"Bearer {token}", "X-Conversation-Id": cid})
        yield Caller(
            c, user.user_id, derive_session_id(user.user_id, cid), user.bu_codes, user.regions
        )


async def _sql(q: str, **params) -> list:
    """测试侧的标准 SQL。直接写 SQL 而不复用被测代码 —— 否则就是拿实现验证实现。"""
    async with app_session() as s:
        return (await s.execute(text(q), params)).all()


# ── 权限收窄 ─────────────────────────────────────────────────────


async def test_inventory_rows_stay_inside_user_scope() -> None:
    """受限用户查到的每一行都在其 BU/区域内，且条数与标准 SQL 相等（S2-c）。

    两条都要：只查「行都在域内」会放过「少返回了域内的行」，
    只查条数会放过「用域外的行凑够了数」。
    """
    async with _as("DEMO_TOKEN_TILE") as me:
        body = (await me.http.get("/api/v1/inventory", params={"limit": 100})).json()
    assert all(r["bu_code"] in me.bu_codes for r in body["data"])
    assert all(r["region"] in me.regions for r in body["data"])
    [(expected,)] = await _sql(
        "SELECT count(*) FROM inventory_batch WHERE bu_code = ANY(:b) AND region = ANY(:r)",
        b=sorted(me.bu_codes),
        r=sorted(me.regions),
    )
    assert body["pagination"]["total_count"] == expected > 0


@pytest.mark.parametrize(
    ("param", "value", "code"),
    [("bu_code", "BU-A", "AUTH_BU_OUT_OF_SCOPE"), ("region", "华南", "AUTH_REGION_OUT_OF_SCOPE")],
)
async def test_narrowing_outside_scope_is_an_explicit_error(param, value, code) -> None:
    """越界收窄必须报错，**不得静默返回空**（S2-c）—— 空结果与「无权限」在用户看来一样。"""
    async with _as("DEMO_TOKEN_TILE") as me:
        r = await me.http.get("/api/v1/inventory", params={param: value})
    assert r.status_code == 403
    assert r.json()["code"] == code


async def test_production_plans_are_region_filtered_via_the_line() -> None:
    """排产计划本身没有 region，区域约束经产线补上 —— 漏补就是全国排产对华东用户可见。

    断言受限用户的总数 **严格小于** 同 BU 的全量：若相等，说明区域过滤没生效
    （夹具保证每个用户域外都有数据，边界-09）。
    """
    async with _as("DEMO_TOKEN_TILE") as me:
        total = (await me.http.get("/api/v1/production-plans")).json()["pagination"]["total_count"]
    [(expected,)] = await _sql(
        "SELECT count(*) FROM production_plan p JOIN production_line pl "
        "  ON pl.line_code = p.line_code "
        " WHERE p.bu_code = ANY(:b) AND pl.region = ANY(:r)",
        b=sorted(me.bu_codes),
        r=sorted(me.regions),
    )
    [(same_bu_all,)] = await _sql(
        "SELECT count(*) FROM production_plan WHERE bu_code = ANY(:b)", b=sorted(me.bu_codes)
    )
    assert total == expected
    assert total < same_bu_all, "区域过滤未生效：受限用户看到了同 BU 的全部排产"


async def test_stale_enum_value_from_the_design_doc_is_rejected() -> None:
    """设计文档里的旧取值「待确认」必须被拒，而不是静默返回 0 行。

    数据库 CHECK 约束是「待评审」。Literal 若照文档写，这条查询会永远返回
    空列表且不报错 —— 模型会如实告诉用户「没有待确认的订单」。
    """
    async with _as("DEMO_TOKEN_GROUP") as me:
        r = await me.http.get("/api/v1/orders", params={"status": "待确认"})
    assert r.status_code == 400
    assert r.json()["code"] == "VAL_INVALID"


async def test_paging_through_ties_neither_repeats_nor_skips() -> None:
    """按下单日期翻完全部订单：不重、不漏。

    同一天有多张单时，仅按日期排序的翻页会让并列行在两页间互换位置 ——
    结果是某张单出现两次、另一张一次都不出现，而且不报任何错。
    """
    async with _as("DEMO_TOKEN_GROUP") as me:
        seen: list[str] = []
        offset, total = 0, None
        while total is None or offset < total:
            body = (
                await me.http.get("/api/v1/orders", params={"limit": 7, "offset": offset})
            ).json()
            total = body["pagination"]["total_count"]
            seen += [r["order_no"] for r in body["data"]]
            offset += 7
    assert len(seen) == len(set(seen)) == total


def _docstring_lines(src: str) -> set[int]:
    """模块/类/函数 docstring 占据的行号。

    这些行是在**解释**规则（「用 LEFT JOIN 会把孤儿行带进来」），不是在违反规则。
    SQL 本身写在 ``text("...")`` 字符串里，所以不能简单跳过全部字符串 —— 只跳 docstring。
    """
    lines: set[int] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                lines.update(range(body[0].lineno, body[0].end_lineno + 1))
    return lines


def test_read_paths_never_use_outer_joins() -> None:
    """🔴 不建外键的补偿手段①的机械保障：读路径一律 INNER JOIN。

    外连接会把指向已不存在父行的孤儿行带进结果，字段一片 NULL，而模型会把
    NULL 当成「这张单没有客户」如实说出去。规则写在文档里靠自觉，写成检查才有效。
    """
    pattern = re.compile(
        r"outerjoin|isouter\s*=\s*True|full\s*=\s*True|\b(LEFT|RIGHT|FULL)\s+(OUTER\s+)?JOIN",
        re.I,
    )
    offenders = []
    for d in ("src/agentsystem/api", "src/agentsystem/orders"):
        for p in pathlib.Path(d).rglob("*.py"):
            src = p.read_text(encoding="utf-8")
            skip = _docstring_lines(src)
            for n, line in enumerate(src.splitlines(), 1):
                if n in skip or line.lstrip().startswith("#"):
                    continue
                if pattern.search(line):
                    offenders.append(f"{p}:{n}")
    assert not offenders, "读路径出现外连接：\n  " + "\n  ".join(offenders)


# ── S5：延误推演与联动范围 ───────────────────────────────────────


async def test_delay_verdict_matches_the_frozen_rubric_for_every_order() -> None:
    """🔒 评分细则 S5-c：对**每一张**有排产的订单，结论与标准 SQL 一致。

    标准 SQL 按细则原文写：每个订单行取末道工序（stage_seq 最大），
    其 ``plan_end > required_date`` 即延误。另写一遍而不调用被测代码。
    """
    standard = dict(
        await _sql(
            """
            WITH last AS (
              SELECT DISTINCT ON (l.line_id) o.order_no, p.plan_end, o.required_date
                FROM sales_order o
                JOIN sales_order_line l ON l.order_no = o.order_no
                JOIN production_plan  p ON p.related_order_line = l.line_id
               ORDER BY l.line_id, p.stage_seq DESC
            )
            SELECT order_no, bool_or(plan_end > required_date) FROM last GROUP BY order_no
            """
        )
    )
    # 阴性对照不可省（夹具规格 S5-04）：全是延误样本时，恒答「会延误」也能全对。
    assert set(standard.values()) == {True, False}

    mismatches = []
    async with _as("DEMO_TOKEN_GROUP") as me:
        for order_no, late in standard.items():
            got = (await me.http.get(f"/api/v1/orders/{order_no}/delay")).json()["data"]
            if got["will_delay"] != late:
                mismatches.append(order_no)
            # 天数与结论须自洽。恰在交期当天完工时天数为 0，两种结论都可能，
            # 所以是「延误 ⇒ ≥0、按期 ⇒ ≤0」，而不是严格的正负。
            assert got["delay_days"] >= 0 if late else got["delay_days"] <= 0, order_no
    assert not mismatches, f"{len(mismatches)} 张订单结论与细则不一致：{mismatches[:5]}"


async def test_delay_days_are_counted_in_business_calendar_days() -> None:
    """延误天数按**上海日历日**算，不按 UTC。

    夹具把延误单的末道工序排在交期后整 5 天（本地零点）。数据库是 UTC，
    直接对时刻取 ``.date()`` 会少算一天 —— 实测踩到：说明里写「延误 4 天」。
    """
    rows = await _sql(
        """
        WITH last AS (
          SELECT DISTINCT ON (l.line_id) o.order_no, o.required_date,
                 (p.plan_end AT TIME ZONE 'Asia/Shanghai')::date AS local_end
            FROM sales_order o
            JOIN sales_order_line l ON l.order_no = o.order_no
            JOIN production_plan  p ON p.related_order_line = l.line_id
           ORDER BY l.line_id, p.stage_seq DESC
        )
        SELECT order_no, max(local_end) - min(required_date) FROM last
         GROUP BY order_no ORDER BY order_no LIMIT 15
        """
    )
    async with _as("DEMO_TOKEN_GROUP") as me:
        for order_no, days in rows:
            got = (await me.http.get(f"/api/v1/orders/{order_no}/delay")).json()["data"]
            assert got["delay_days"] == days, order_no


async def test_downstream_matches_the_rubric_definition_for_every_plan() -> None:
    """🔒 评分细则 S5-d：对**每一道**关联订单行的工序，下游集合等于
    「同一订单行、stage_seq 更大的计划」。"""
    plans = await _sql(
        "SELECT plan_no, related_order_line, stage_seq FROM production_plan "
        " WHERE related_order_line IS NOT NULL"
    )
    by_line: dict[int, list] = {}
    for p in plans:
        by_line.setdefault(p.related_order_line, []).append(p)
    # 夹具规格 S5-06：至少要有三道连续工序的组，否则「延一道影响后两道」无从验证。
    assert any(len(v) >= 3 for v in by_line.values())

    async with _as("DEMO_TOKEN_GROUP") as me:
        for p in plans:
            expected = {
                q.plan_no for q in by_line[p.related_order_line] if q.stage_seq > p.stage_seq
            }
            got = (await me.http.get(f"/api/v1/production-plans/{p.plan_no}/downstream")).json()
            assert {r["plan_no"] for r in got["data"]} == expected, p.plan_no


# ── S3：创建订单 ─────────────────────────────────────────────────


@pytest.fixture
async def created() -> AsyncIterator[list[str]]:
    """收集测试中创建的订单号，结束时清掉，避免污染开发库的种子数据形态。

    审计行不清 —— 它是仅追加的，删不掉，这是正确行为（conftest 同理）。
    """
    orders: list[str] = []
    yield orders
    if orders:
        async with app_session() as s:
            await s.execute(
                text("DELETE FROM sales_order_line WHERE order_no = ANY(:o)"), {"o": orders}
            )
            await s.execute(text("DELETE FROM sales_order WHERE order_no = ANY(:o)"), {"o": orders})


async def _stock_line(qty: str = "1", *, bu: str | None = None) -> tuple[str, dict]:
    """从库里挑一批确实可发的库存，拼一行能通过复检的订单行。

    Args:
        qty: 订单行需求量。
        bu: 限定事业部；受限用户的用例要挑其域内的库存，否则会先撞上别的拦截。
    """
    [row] = await _sql(
        "SELECT bu_code, product_code, color_code, grade, uom, spec, sum(qty_available) q "
        "  FROM inventory_batch WHERE qc_status = '合格' "
        "   AND (CAST(:bu AS text) IS NULL OR bu_code = :bu) "
        " GROUP BY bu_code, product_code, color_code, grade, uom, spec, batch_no "
        "HAVING sum(qty_available) >= 10 ORDER BY 1, 2 LIMIT 1",
        bu=bu,
    )
    return row.bu_code, {
        "product_code": row.product_code,
        "spec": row.spec,
        "color_code": row.color_code,
        "grade_required": row.grade,
        "batch_policy": "SAME_BATCH",
        "qty": qty,
        "uom": row.uom,
        "unit_price": "12.5",
    }


def _payload(bu: str, line: dict, region: str = "华东") -> dict:
    """一份符合 CreateOrderPayload 契约的载荷。"""
    return {
        "bu_code": bu,
        "region": region,
        "customer_code": "CUST-TEST-01",
        "customer_name": "测试客户-甲",
        "order_type": "经销",
        "required_date": "2026-12-31",
        "lines": [line],
    }


async def _mint_for(me: Caller, payload: dict, *, session_id: str | None = None) -> str:
    """以调用方的会话铸造一枚待确认意图（模拟 P3 下单子图的确认步骤）。"""
    async with app_session() as s:
        minted = await mint_intent(
            s,
            session_id=session_id or me.session_id,
            user_id=me.user_id,
            trace_id="test-mint",
            intent_type="create_order",
            payload=payload,
        )
    return minted.confirm_token


async def test_create_order_writes_order_lines_and_both_audit_rows(created) -> None:
    """正常路径 + 评分细则 S3-d：attempt 与 outcome 两行齐全，after_value 含新订单号。"""
    bu, line = await _stock_line()
    async with _as("DEMO_TOKEN_GROUP") as me:
        tok = await _mint_for(me, _payload(bu, line))
        r = await me.http.post("/api/v1/orders", json={"confirm_token": tok})
    assert r.status_code == 201, r.text
    order = r.json()["data"]
    created.append(order["order_no"])
    assert order["status"] == "已确认"
    assert [ln["line_no"] for ln in order["lines"]] == [1]

    audit = await _sql(
        "SELECT phase, status, after_value FROM audit_log "
        " WHERE trace_id = :t AND tool_name = 'create_order' ORDER BY id",
        t=r.headers["x-trace-id"],
    )
    assert [a.phase for a in audit] == ["attempt", "outcome"]
    assert audit[1].status == "success"
    assert audit[1].after_value["order_no"] == order["order_no"]


async def test_resubmitting_the_same_token_returns_the_original_order(created) -> None:
    """评分细则 S3-c：同一令牌重复提交，订单只增一行，第二次返回原订单号。"""
    bu, line = await _stock_line()
    async with _as("DEMO_TOKEN_GROUP") as me:
        tok = await _mint_for(me, _payload(bu, line))
        first = await me.http.post("/api/v1/orders", json={"confirm_token": tok})
        second = await me.http.post("/api/v1/orders", json={"confirm_token": tok})
    created.append(first.json()["data"]["order_no"])
    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json()["data"]["order_no"] == first.json()["data"]["order_no"]
    [(n,)] = await _sql("SELECT count(*) FROM sales_order WHERE confirm_token = :t", t=tok)
    assert n == 1


async def test_business_fields_in_the_request_body_are_rejected(created) -> None:
    """评分细则 S3-b：确认请求只带令牌；带业务参数的请求**须被拒**。

    且被拒时令牌**不得被消费** —— 拒绝发生在消费之前，用户改正请求后仍可确认。
    """
    bu, line = await _stock_line()
    async with _as("DEMO_TOKEN_GROUP") as me:
        tok = await _mint_for(me, _payload(bu, line))
        r = await me.http.post("/api/v1/orders", json={"confirm_token": tok, "qty": 99999})
    assert r.status_code == 400
    assert r.json()["code"] == "VAL_INVALID"
    [(state,)] = await _sql("SELECT state FROM write_intent WHERE confirm_token = :t", t=tok)
    assert state == "pending"


async def test_insufficient_stock_is_a_non_retryable_rejection_and_spends_the_token(
    created,
) -> None:
    """库存复检不通过 → 409 ``BIZ_INSUFFICIENT_STOCK``，``retryable=false``。

    同时令牌已作废（至多一次）：若消费与业务写入同事务，这次回滚会把令牌恢复成
    pending，同一次确认就能反复提交直到库存恰好够 —— 人只确认过一次。
    """
    bu, line = await _stock_line(qty="99999999")
    async with _as("DEMO_TOKEN_GROUP") as me:
        tok = await _mint_for(me, _payload(bu, line))
        r = await me.http.post("/api/v1/orders", json={"confirm_token": tok})
        again = await me.http.post("/api/v1/orders", json={"confirm_token": tok})
    assert r.status_code == 409
    assert (r.json()["code"], r.json()["retryable"]) == ("BIZ_INSUFFICIENT_STOCK", False)
    assert again.json()["code"] == "AUTH_CONFIRM_TOKEN_SPENT"
    [(n,)] = await _sql("SELECT count(*) FROM sales_order WHERE confirm_token = :t", t=tok)
    assert n == 0


async def test_a_token_minted_for_another_session_cannot_be_used(created) -> None:
    """别的会话的令牌不能在本会话兑现 —— 否则「谁在确认」就无从谈起。"""
    bu, line = await _stock_line()
    async with _as("DEMO_TOKEN_GROUP") as me:
        tok = await _mint_for(me, _payload(bu, line), session_id="someone-elses-session")
        r = await me.http.post("/api/v1/orders", json={"confirm_token": tok})
    assert r.status_code == 403
    assert r.json()["code"] == "AUTH_CONFIRM_TOKEN_INVALID"


async def test_payload_outside_the_callers_scope_is_rejected_at_write_time(created) -> None:
    """载荷里的事业部越出调用方权限时，写入侧必须再拦一次。

    铸造意图的下单子图理应已经校验过；这里再查，是因为写操作的权限判定
    不能只信上游 —— 一个有缺陷的子图就足以在用户无权的事业部里下单。
    """
    _, line = await _stock_line()
    async with _as("DEMO_TOKEN_TILE") as me:
        outside = next(b for b in ("BU-A", "BU-B", "BU-C") if b not in me.bu_codes)
        tok = await _mint_for(me, _payload(outside, line))
        r = await me.http.post("/api/v1/orders", json={"confirm_token": tok})
    assert r.status_code == 403
    assert r.json()["code"] == "AUTH_BU_OUT_OF_SCOPE"
    [(n,)] = await _sql("SELECT count(*) FROM sales_order WHERE confirm_token = :t", t=tok)
    assert n == 0


async def test_payload_region_outside_the_callers_scope_is_rejected(created) -> None:
    """载荷里的**区域**越权，只有写入侧那一行显式复核在挡。

    事业部越权有两道防线（显式复核 + 库存复检的 scoped_select），删掉一道另一道
    照样拦 —— 变异测试正是这样发现的。但库存复检只按用户区域过滤**仓库**，不看
    **订单**的区域，所以区域越权没有第二道防线。这条用例就是为它单独写的。
    """
    async with _as("DEMO_TOKEN_TILE") as me:
        [bu] = sorted(me.bu_codes)
        _, line = await _stock_line(bu=bu)
        outside = next(r for r in ("华东", "华南", "华北", "西南") if r not in me.regions)
        tok = await _mint_for(me, _payload(bu, line, region=outside))
        r = await me.http.post("/api/v1/orders", json={"confirm_token": tok})
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "AUTH_REGION_OUT_OF_SCOPE"
    [(n,)] = await _sql("SELECT count(*) FROM sales_order WHERE confirm_token = :t", t=tok)
    assert n == 0


def test_create_order_request_accepts_nothing_but_the_token() -> None:
    """请求模型层面再钉一次：除令牌外没有任何字段。

    有人日后「顺手」给请求体加个 note 字段，这条会转红 —— 那正是需要停下来
    想一想宪法第十条的时候。
    """
    from agentsystem.api.schemas import CreateOrderRequest

    assert set(CreateOrderRequest.model_fields) == {"confirm_token"}
    assert CreateOrderRequest.model_config.get("extra") == "forbid"
