-- ═══════════════════════════════════════════════════════════════════
-- P1.3.5 收尾 · 把 Dify 从 postgres（超级用户）切到 dify_owner
--
-- 🔴 需要停机。执行前请先 docker compose stop（Dify 侧），执行后改
--    Dify 的 .env 再启动。步骤见 docs/DEPLOYMENT.md §3。
--
-- 为什么必须切：
--   Dify 当前以 postgres 连库，而 **超级用户绕过一切 ACL**。这意味着
--   任何能拿到 Dify 数据库凭据的路径，都能对 agentsystem 执行
--   DROP EVENT TRIGGER 后随意改写 audit_log —— P1.3.4 辛苦搭的三层
--   防护对它完全无效。scripts/04 只关掉了非超级用户那条路径。
--
-- ⚠️ 刻意**不用** REASSIGN OWNED BY postgres TO dify_owner：
--    该命令除当前库的对象外，还会改「共享对象」的属主，包括
--    postgres / template0 / template1 等数据库。一条命令波及整个集群，
--    出事无法局部回滚。下面改为逐类对象显式 ALTER，范围可控。
--
-- 用法（对 dify 与 dify_plugin 两个库各跑一次）：
--   psql -U postgres -d dify        -f scripts/05_switch_dify_to_dify_owner.sql
--   psql -U postgres -d dify_plugin -f scripts/05_switch_dify_to_dify_owner.sql
-- ═══════════════════════════════════════════════════════════════════

\set ON_ERROR_STOP on

\echo '── 当前库中属主为 postgres 的对象数 ──'
SELECT count(*) FILTER (WHERE relkind IN ('r','p'))  AS 表,
       count(*) FILTER (WHERE relkind = 'S')          AS 序列,
       count(*) FILTER (WHERE relkind IN ('v','m'))   AS 视图
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname NOT IN ('pg_catalog','information_schema')
   AND pg_get_userbyid(c.relowner) = 'postgres';

-- ── 库属主 ──
ALTER DATABASE :"DBNAME" OWNER TO dify_owner;

-- ── schema ──
SELECT format('ALTER SCHEMA %I OWNER TO dify_owner', nspname)
  FROM pg_namespace
 WHERE nspname NOT IN ('pg_catalog','information_schema','pg_toast')
   AND pg_get_userbyid(nspowner) = 'postgres'
\gexec

-- ── 表 / 分区表 / 序列 / 视图 / 物化视图 ──
-- 逐类生成 ALTER。序列若被表的 IDENTITY 拥有，属主随表走，重复 ALTER 无害。
SELECT format('ALTER %s %I.%I OWNER TO dify_owner',
              CASE c.relkind WHEN 'S' THEN 'SEQUENCE'
                             WHEN 'v' THEN 'VIEW'
                             WHEN 'm' THEN 'MATERIALIZED VIEW'
                             ELSE 'TABLE' END,
              n.nspname, c.relname)
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')
   AND c.relkind IN ('r','p','S','v','m')
   AND pg_get_userbyid(c.relowner) = 'postgres'
\gexec

-- ── 函数与存储过程 ──
SELECT format('ALTER ROUTINE %I.%I(%s) OWNER TO dify_owner',
              n.nspname, p.proname, pg_get_function_identity_arguments(p.oid))
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname NOT IN ('pg_catalog','information_schema')
   AND pg_get_userbyid(p.proowner) = 'postgres'
\gexec

\echo '── 切换后仍属 postgres 的对象数（应为 0）──'
SELECT count(*) AS 残留
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')
   AND c.relkind IN ('r','p','S','v','m')
   AND pg_get_userbyid(c.relowner) = 'postgres';

\echo '  ✅ 属主已转移。接下来改 Dify 的 .env：'
\echo '     DB_USERNAME=dify_owner'
\echo '     DB_PASSWORD=<PG_DIFY_OWNER_PASSWORD 的值>'
\echo '     然后 docker compose up -d'
