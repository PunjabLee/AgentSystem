-- ═══════════════════════════════════════════════════════════════════
-- 扩展预建 · 须以超级用户执行
--
-- CREATE EXTENSION 要求超级用户，而 Alembic 以 app_migrator 运行，故不能
-- 放进迁移。恢复演练里已有同类前例（vector 扩展），见 restore_drill.sh。
--
-- 依赖关系写在这里，是为了让「迁移报 relation does not exist」时能一眼
-- 看出是扩展没建，而不是迁移写错了。
-- ═══════════════════════════════════════════════════════════════════

\set ON_ERROR_STOP on

-- pg_trgm：支撑 product.product_name 的模糊匹配（§4.1.1）。
--   用户说「查一下白色岩板的库存」，需要把自然语言名词映射到 product_code；
--   没有它，GIN + gin_trgm_ops 索引建不出来，名称检索只能退化成 LIKE '%…%'
--   全表扫描 —— 这正是 S2–S5 四个场景共同的入口。
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- vector：Dify 的向量存储用。此处一并声明，便于全新环境一次建齐。
CREATE EXTENSION IF NOT EXISTS vector;

\echo '  ✅ 扩展就绪'
