-- ═══════════════════════════════════════════════════════════════════
-- P1.3.5 补齐 · 第四个角色与库级隔离
--
-- 须以超级用户执行。
--
-- 背景（2026-09-03 核对 WBS 时发现的缺口）：
--   前三个角色建好后，`dify` 库做了 REVOKE ALL FROM PUBLIC，
--   但 **`agentsystem` 反而保持默认 ACL —— PUBLIC 可连**。方向做反了：
--   真正需要防的是别人连进 agentsystem，不是我们连不进 dify。
--
-- ⚠️ 本脚本只能关掉「非超级用户」这条路径。Dify 当前以 `postgres`
--    （超级用户）连库，超级用户绕过一切 ACL —— 它仍能 DROP EVENT TRIGGER
--    并改写 audit_log。彻底封堵需要把 Dify 切到 dify_owner，
--    见 scripts/05_switch_dify_to_dify_owner.sql（需停机，未自动执行）。
-- ═══════════════════════════════════════════════════════════════════

\set ON_ERROR_STOP on

-- ── 第四个角色：Dify 专用，非超级用户 ──
-- 口令由调用方以 -v dify_owner_pw=... 传入。
SELECT format('CREATE ROLE dify_owner LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB', :'dify_owner_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dify_owner')
\gexec

-- ── agentsystem 库级隔离 ──
-- 先收回 PUBLIC，再逐个显式授予。顺序不能反：先授后收会把刚授的一起收掉。
REVOKE ALL ON DATABASE agentsystem FROM PUBLIC;
GRANT CONNECT ON DATABASE agentsystem TO app_migrator;
GRANT CONNECT ON DATABASE agentsystem TO app_rw;
GRANT CONNECT ON DATABASE agentsystem TO app_ro;

-- dify_owner 刻意不授。写成显式 REVOKE 而非「不写 GRANT」，
-- 是为了让意图在代码里可见 —— 后人加一条 GRANT ... TO PUBLIC 时，
-- 这一行会提醒他这里有个刻意的边界。
REVOKE ALL ON DATABASE agentsystem FROM dify_owner;

-- ── dify 库：dify_owner 需要完全权限（切换后它是属主）──
GRANT CONNECT ON DATABASE dify TO dify_owner;

\echo '  ✅ dify_owner 已建；agentsystem 已收回 PUBLIC 的 CONNECT'
