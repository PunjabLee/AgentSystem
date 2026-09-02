-- ═══════════════════════════════════════════════════════════════════
-- P1.3.5（续）· agentsystem 库内的 schema 权限与默认权限
-- 须以 app_migrator 身份连接 agentsystem 库执行。
-- ═══════════════════════════════════════════════════════════════════
\set ON_ERROR_STOP on

-- app_migrator 是属主，天然有全部权限；这里只配另外两个角色
GRANT USAGE ON SCHEMA public TO app_rw, app_ro;

-- 🔴 ALTER DEFAULT PRIVILEGES 的 FOR ROLE 必须指向「实际建表的角色」
--    这是经典陷阱：写成 FOR ROLE app_rw 则完全不生效，因为表不是它建的。
ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public
  GRANT SELECT ON TABLES TO app_ro;

-- audit_log 的收紧在 P1.3.4 单独处理 —— 它需要在建表之后
-- REVOKE 掉这里刚给出的 UPDATE / DELETE。
