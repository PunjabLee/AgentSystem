-- ═══════════════════════════════════════════════════════════════════
-- P1.3.4 第三层 · DDL 事件触发器
--
-- 须以超级用户执行 —— CREATE EVENT TRIGGER 要求 superuser，而 Alembic
-- 以 app_migrator 运行，故不能放进迁移。
--
-- 为什么必须有这一层（P1 详细设计 §3.4 实测）：
--   属主一句 ALTER TABLE audit_log DISABLE TRIGGER ... 即可拆掉前两层，
--   而 Alembic 正是以属主身份运行，这条路径天天都在用。
--   事件触发器在 DDL 执行时触发，**不区分调用者角色**，对属主与超级用户
--   均有效。
--
-- 能力边界：超级用户仍可 DROP EVENT TRIGGER 后绕过。数据库内部机制的
--   上限就在这里，再往上需要 WORM 存储、审计日志外发或 pgAudit。
--   PoC 接受该边界，规模化落地前必须补外部手段。
--
-- 副作用（刻意的）：本触发器会拦住**所有**对 audit_log 的 ALTER，包括
--   后续合法的 schema 变更。届时流程是「超级用户 DROP EVENT TRIGGER →
--   迁移 → 重建」，且该操作本身应被记录。审计表的结构变更就该是需要
--   显式解锁的动作。
-- ═══════════════════════════════════════════════════════════════════

\set ON_ERROR_STOP on

CREATE OR REPLACE FUNCTION guard_audit_ddl() RETURNS event_trigger
LANGUAGE plpgsql AS $fn$
DECLARE r record;
BEGIN
  FOR r IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP
    IF r.object_identity LIKE '%audit_log%' AND r.command_tag = 'ALTER TABLE' THEN
      RAISE EXCEPTION '禁止对 audit_log 执行 ALTER（宪法第一条）：%', r.object_identity;
    END IF;
  END LOOP;
END $fn$;

DROP EVENT TRIGGER IF EXISTS trg_guard_audit_ddl;
CREATE EVENT TRIGGER trg_guard_audit_ddl
  ON ddl_command_end EXECUTE FUNCTION guard_audit_ddl();

\echo '  ✅ 事件触发器已建（拦截对 audit_log 的一切 ALTER）'
