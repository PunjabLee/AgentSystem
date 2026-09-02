-- ═══════════════════════════════════════════════════════════════════
-- P1.3.5 · 角色与授权
--
-- 必须在任何建表动作之前执行。原因：ALTER DEFAULT PRIVILEGES FOR ROLE
-- 只对「此后由该角色创建的对象」生效，排在建表之后则两张表拿不到默认权限。
--
-- 四个角色的职责边界（P1 详细设计 §2.5 / §3.4）：
--   app_migrator  agentsystem 库属主，仅 Alembic 使用，有 CREATE
--   app_rw        运行时账号，对 audit_log 仅 INSERT + SELECT
--   app_ro        对 dify 库只读（宪法第三条：向量库写入权归 Dify 独占）
--
-- 🔴 关键：迁移账号与运行时账号必须分离。若 Alembic 用 app_rw 跑，
--    app_rw 即成为 audit_log 的属主，而属主可 DISABLE TRIGGER，
--    P1.3.4 的防篡改层整层失效（已实测，见 P1 详细设计 §3.4）。
--
-- 用法（口令经 psql 变量传入，不落盘）：
--   psql -v migrator_pw=... -v app_pw=... -v ro_pw=... -f 01_roles_and_grants.sql
--
-- 注：口令用 psql 的 :'var' 插值，且**不能写在 DO $$ $$ 块内**——
--     psql 不对美元引号内的内容做变量替换。故用 \gexec 模式。
-- ═══════════════════════════════════════════════════════════════════

\set ON_ERROR_STOP on

-- ── 1. 建角色（不存在才建），全部 NOSUPERUSER ────────────────────
SELECT format('CREATE ROLE app_migrator LOGIN PASSWORD %L NOSUPERUSER CREATEDB', :'migrator_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_migrator')
\gexec
SELECT format('CREATE ROLE app_rw LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB', :'app_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rw')
\gexec
SELECT format('CREATE ROLE app_ro LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB', :'ro_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_ro')
\gexec

-- ── 2. 幂等地同步口令（重跑脚本时以传入值为准）──────────────────
SELECT format('ALTER ROLE app_migrator PASSWORD %L', :'migrator_pw') \gexec
SELECT format('ALTER ROLE app_rw       PASSWORD %L', :'app_pw')      \gexec
SELECT format('ALTER ROLE app_ro       PASSWORD %L', :'ro_pw')       \gexec

-- ── 3. agentsystem 库属主移交给 app_migrator ─────────────────────
ALTER DATABASE agentsystem OWNER TO app_migrator;

-- ── 4. dify 库：仅 app_ro 可连（宪法第三条）──────────────────────
--    app_rw 也不给 —— 应用侧读向量走 Dify 的检索 API，不直连向量表，
--    这样「绕过 Dify 直写向量库」在授权层就不可能发生。
REVOKE ALL ON DATABASE dify FROM PUBLIC;
GRANT CONNECT ON DATABASE dify TO app_ro;

\echo '  ✅ 角色与库级授权完成'
