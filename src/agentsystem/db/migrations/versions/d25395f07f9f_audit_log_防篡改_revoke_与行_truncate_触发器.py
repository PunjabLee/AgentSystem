"""audit_log 防篡改：REVOKE 与行/TRUNCATE 触发器

本迁移实现三层防护中的前两层。第三层（ddl_command_end 事件触发器）
需要超级用户权限，Alembic 以 app_migrator 运行，故单独放在
scripts/03_audit_event_trigger.sql，由 postgres 执行。

三层缺一不可 —— 实测（P1 详细设计 §3.4）：
  · 只有第一层：属主可直接 UPDATE
  · 只有一二层：属主一句 ALTER TABLE ... DISABLE TRIGGER 即可拆掉
  · 三层齐备：属主与超级用户的 DISABLE TRIGGER 均被拦


Revision ID: d25395f07f9f
Revises: dd8bbee17642
Create Date: 2026-09-02 21:56:41.606166
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d25395f07f9f"
down_revision: str | None = "dd8bbee17642"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
-- ── 第一层：权限。收回 ALTER DEFAULT PRIVILEGES 自动授予的 UPDATE/DELETE ──
-- 默认权限给了四种操作，审计表只允许两种。这一层拦运行时账号。
REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM app_rw;
REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM PUBLIC;
GRANT INSERT, SELECT ON audit_log TO app_rw;
GRANT USAGE, SELECT ON SEQUENCE audit_log_id_seq TO app_rw;
GRANT SELECT ON audit_log TO app_ro;

-- ── 第二层：触发器。REVOKE 对表属主无效，但触发器对属主有效 ──
-- 实测确认：属主直接 UPDATE / TRUNCATE 会被下面两个触发器拦住
-- （P1 详细设计 §3.4 的三层攻击实测）。
CREATE OR REPLACE FUNCTION deny_audit_mutation() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
  RAISE EXCEPTION 'audit_log 仅允许追加，不得修改或删除（宪法第一条）';
END $fn$;

CREATE TRIGGER trg_audit_no_update_delete
  BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION deny_audit_mutation();

-- TRUNCATE 既不触发 BEFORE UPDATE/DELETE，也不受 REVOKE UPDATE,DELETE 约束，
-- 是两位评审专家共同指出的绕过口，须单独用语句级触发器堵上。
CREATE TRIGGER trg_audit_no_truncate
  BEFORE TRUNCATE ON audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION deny_audit_mutation();
"""

DOWNGRADE_SQL = """
DROP TRIGGER IF EXISTS trg_audit_no_truncate ON audit_log;
DROP TRIGGER IF EXISTS trg_audit_no_update_delete ON audit_log;
DROP FUNCTION IF EXISTS deny_audit_mutation();
GRANT UPDATE, DELETE ON audit_log TO app_rw;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
