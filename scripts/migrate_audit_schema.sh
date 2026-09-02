#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# audit_log 的结构变更流程（P1.3.4 第三层的配套逃生口）
#
# 事件触发器刻意拦下对 audit_log 的一切 ALTER，包括合法迁移。审计表的
# 结构变更就该是需要显式解锁的动作，不该混在日常 `alembic upgrade` 里。
#
#   解锁（超级用户）→ 迁移（app_migrator）→ 重新上锁（超级用户）
#
# 🔴 trap 保证无论迁移成功、失败还是被 Ctrl-C，都会重新上锁 —— 否则
#    进程中途死掉会留下一张无保护的审计表，而没人会注意到。
#
# 用法：bash scripts/migrate_audit_schema.sh
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

CONTAINER="${PG_CONTAINER:-docker-db_postgres-1}"
DB="${PG_DATABASE:-agentsystem}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

relock() {
  echo "── 重新上锁 ──"
  docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -q \
    < "$REPO_ROOT/scripts/03_audit_event_trigger.sql"
  # 上锁必须被确认，不能只看退出码：这是整个流程唯一不能失败的一步。
  local n
  n=$(docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -tAq -c \
      "SELECT count(*) FROM pg_event_trigger WHERE evtname = 'trg_guard_audit_ddl';")
  if [ "$n" != "1" ]; then
    echo "🔴 上锁失败！audit_log 当前无 DDL 保护，必须立即人工处理。" >&2
    exit 1
  fi
  echo "  ✅ 事件触发器已恢复"
}
trap relock EXIT

echo "── 解锁（超级用户）──"
docker exec -i "$CONTAINER" psql -U postgres -d "$DB" -q \
  -c "DROP EVENT TRIGGER IF EXISTS trg_guard_audit_ddl;"
echo "  ⚠️  audit_log 的 DDL 保护已临时移除"

echo "── 迁移（app_migrator）──"
cd "$REPO_ROOT" && uv run alembic upgrade head
