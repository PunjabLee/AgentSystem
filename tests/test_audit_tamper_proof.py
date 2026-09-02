"""P1.3.4 审计表防篡改三层的机械断言（宪法第一条）。

三层缺一层即可被绕过，故每层单独断言，且断言 **SQLSTATE** 而非"报了个错"——
错误来源必须对得上层次，否则一个拼错的列名也能让测试变绿（这在开发过程中
真实发生过一次）。

  第一层 REVOKE        → 42501 insufficient_privilege
  第二层 行/语句触发器  → P0001 raise_exception
  第三层 事件触发器     → P0001 raise_exception（拦 ALTER）

第三层的超级用户路径不在此覆盖：超管凭据刻意不进 Settings，
由 scripts/03_audit_event_trigger.sql 配套的人工演练验证。
"""

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

INSUFFICIENT_PRIVILEGE = "42501"
RAISE_EXCEPTION = "P0001"


def _sqlstate(exc: DBAPIError) -> str | None:
    """从 SQLAlchemy 包装的异常里取出 PostgreSQL SQLSTATE。"""
    return getattr(exc.orig, "sqlstate", None)


def _expect_blocked(engine: Engine, sql: str, params: dict, sqlstate: str) -> None:
    """断言语句被拒，且拒绝原因来自预期的那一层。"""
    with pytest.raises(DBAPIError) as excinfo:
        with engine.begin() as conn:
            conn.execute(text(sql), params)
    actual = _sqlstate(excinfo.value)
    assert actual == sqlstate, f"被拦了，但来自错误的层：SQLSTATE={actual}，期望 {sqlstate}"


# ── 第一层：REVOKE 拦运行时账号 ───────────────────────────────────
@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_log SET user_id = 'hacked' WHERE trace_id = :t",
        "DELETE FROM audit_log WHERE trace_id = :t",
        "TRUNCATE audit_log",
    ],
    ids=["update", "delete", "truncate"],
)
def test_layer1_app_rw_cannot_mutate(rw_engine: Engine, seeded_audit_row: str, sql: str) -> None:
    """app_rw 对 audit_log 只应有 INSERT/SELECT。"""
    _expect_blocked(rw_engine, sql, {"t": seeded_audit_row}, INSUFFICIENT_PRIVILEGE)


def test_layer1_app_rw_can_still_insert(rw_engine: Engine) -> None:
    """阳性对照：收紧权限不能把正常审计写入也一并掐掉。"""
    with rw_engine.begin() as conn:
        new_id = conn.execute(
            text(
                "INSERT INTO audit_log "
                "(phase, user_id, action_type, trace_id, session_id, source, tool_name) "
                "VALUES ('attempt', 'probe-user', 'read', "
                "'p134-positive', 'probe-sess', 'test', 'p134-positive-control') "
                "RETURNING id"
            )
        ).scalar()
    assert new_id is not None


# ── 第二层：触发器拦表属主（REVOKE 对属主无效）────────────────────
@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_log SET user_id = 'hacked' WHERE trace_id = :t",
        "DELETE FROM audit_log WHERE trace_id = :t",
        "TRUNCATE audit_log",
    ],
    ids=["update", "delete", "truncate"],
)
def test_layer2_owner_cannot_mutate(owner_engine: Engine, seeded_audit_row: str, sql: str) -> None:
    """表属主绕过 REVOKE，但绕不过触发器。

    注意 seeded_audit_row 是必需依赖：空表上的 UPDATE/DELETE 匹配 0 行，
    FOR EACH ROW 触发器不会触发，语句会静默成功。
    """
    _expect_blocked(owner_engine, sql, {"t": seeded_audit_row}, RAISE_EXCEPTION)


# ── 第三层：事件触发器拦 DDL（前两层的拆卸口）─────────────────────
@pytest.mark.parametrize(
    "sql",
    [
        "ALTER TABLE audit_log DISABLE TRIGGER trg_audit_no_update_delete",
        "ALTER TABLE audit_log DISABLE TRIGGER trg_audit_no_truncate",
        "ALTER TABLE audit_log DROP COLUMN evidence_ref",
    ],
    ids=["disable-row-trigger", "disable-truncate-trigger", "drop-column"],
)
def test_layer3_owner_cannot_alter(owner_engine: Engine, sql: str) -> None:
    """Alembic 以属主身份运行，这条路径每天都在用，必须堵死。"""
    _expect_blocked(owner_engine, sql, {}, RAISE_EXCEPTION)


def test_triggers_are_enabled(owner_engine: Engine) -> None:
    """收尾：确认两个触发器仍处于启用态（tgenabled = 'O'）。"""
    with owner_engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT tgname, tgenabled FROM pg_trigger "
                "WHERE tgrelid = 'audit_log'::regclass AND NOT tgisinternal"
            )
        ).all()
    assert {r.tgname for r in rows} == {"trg_audit_no_update_delete", "trg_audit_no_truncate"}
    assert all(r.tgenabled == "O" for r in rows), f"存在被禁用的触发器: {rows}"
