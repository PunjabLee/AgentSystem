#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# P1.5.3 恢复演练（P1 出口判据 3）
#
# 恢复到**全新容器**而非同集群新建库。后者证明不了角色能恢复 ——
# 角色是集群级对象，同集群里它们本来就在，比对必然通过，是自欺。
#
# 判据：恢复后 audit_log 与 write_intent 的行数与备份时记录的一致。
#
# 用法：make restore-drill  或  bash scripts/restore_drill.sh [备份目录]
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${1:-$(ls -dt "$REPO_ROOT"/backups/*/ 2>/dev/null | head -1)}"
BACKUP_DIR="${BACKUP_DIR%/}"
TMP_CONTAINER="agentsystem-restore-drill"
IMAGE="${PG_IMAGE:-pgvector/pgvector:pg18}"
TMP_PW="drill-$(date +%s)"

[ -d "$BACKUP_DIR" ] || { echo "❌ 找不到备份目录，先跑 make backup" >&2; exit 1; }
echo "使用备份: $BACKUP_DIR"
cat "$BACKUP_DIR/manifest.txt" | sed 's/^/  /'

cleanup() { docker rm -f "$TMP_CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

echo "── 起一个干净实例 ──"
docker run -d --name "$TMP_CONTAINER" -e POSTGRES_PASSWORD="$TMP_PW" "$IMAGE" >/dev/null
for _ in $(seq 1 45); do
  docker exec "$TMP_CONTAINER" pg_isready -U postgres >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$TMP_CONTAINER" pg_isready -U postgres >/dev/null || { echo "❌ 实例未就绪" >&2; exit 1; }

echo "── 恢复角色 ──"
docker exec -i "$TMP_CONTAINER" psql -U postgres -q < "$BACKUP_DIR/roles.sql" 2>&1 \
  | grep -viE "already exists|^$" | sed 's/^/  /' || true

echo "── 建库并恢复 agentsystem ──"
docker exec -i "$TMP_CONTAINER" psql -U postgres -q -c "CREATE DATABASE agentsystem OWNER app_migrator;"
# 扩展必须由超级用户预建。首轮演练实测：带 --role=app_migrator 恢复时
# CREATE EXTENSION vector 报 "Must be superuser"，转储里的扩展就静默丢了。
docker exec -i "$TMP_CONTAINER" psql -U postgres -q -d agentsystem \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"
docker exec -i "$TMP_CONTAINER" pg_restore -U postgres -d agentsystem --no-owner --role=app_migrator \
  < "$BACKUP_DIR/agentsystem.dump" 2>&1 \
  | grep -viE "^$|already exists|multiple primary keys" | head -5 | sed 's/^/  /' || true

echo ""
echo "── 比对行数 ──"
EXPECTED="$(cat "$BACKUP_DIR/rowcounts.txt" | tr -d ' ')"
ACTUAL="$(docker exec -i "$TMP_CONTAINER" psql -U postgres -d agentsystem -tAq -c \
  "SELECT 'audit_log='||(SELECT count(*) FROM audit_log)||
          ' write_intent='||(SELECT count(*) FROM write_intent)" | tr -d ' ')"
echo "  备份时: $EXPECTED"
echo "  恢复后: $ACTUAL"

# pg_dump 会带走触发器函数、行触发器，**也带走事件触发器**——
# 但 CREATE EVENT TRIGGER 要求超级用户，而恢复走 --role=app_migrator，
# 这一句必然失败。失败信息淹在 pg_restore 的输出里，不显式重建 + 复验的话，
# 新实例的审计表就少一层保护而无人察觉。
echo ""
echo "── 防篡改层的恢复情况 ──"
docker exec -i "$TMP_CONTAINER" psql -U postgres -d agentsystem -tAq -c \
  "SELECT '  行/TRUNCATE 触发器: '||count(*)||' 个' FROM pg_trigger
    WHERE tgrelid='audit_log'::regclass AND NOT tgisinternal;"
EVT="$(docker exec -i "$TMP_CONTAINER" psql -U postgres -tAq -c \
  "SELECT count(*) FROM pg_event_trigger WHERE evtname='trg_guard_audit_ddl';")"
if [ "$EVT" = "0" ]; then
  echo "  ⚠️  事件触发器未恢复（转储里有，但建它要超级用户），按手册重建 ——"
  docker exec -i "$TMP_CONTAINER" psql -U postgres -d agentsystem -q \
    < "$REPO_ROOT/scripts/03_audit_event_trigger.sql" 2>&1 | grep -v NOTICE | sed 's/^/     /'
fi

# 「恢复成功」必须意味着新实例与原实例保护等同，否则演练只证明了数据回来了、
# 没证明防线回来了。三层逐一实测，任一层缺失即判 FAIL。
echo "── 复验三层防护 ──"
LAYERS_OK=1
probe_blocked() {  # $1=描述 $2=角色 $3=SQL
  # ⚠️ 先把输出收进变量再判断，**不要**写成 `psql ... | grep -q ERROR`。
  #    本脚本开了 set -o pipefail：psql 报错时退出非零，会让整条管道判失败，
  #    即使 grep 确实找到了 ERROR —— 判定正好反转，「拦住了」被报成「没拦住」。
  #    这个坑真实发生过一次，全部三层被误判为 FAIL。
  local out
  out="$(docker exec -i "$TMP_CONTAINER" psql -U postgres -d agentsystem -tAq \
         -c "SET ROLE $2; $3" 2>&1 || true)"
  if printf '%s' "$out" | grep -qi "ERROR"; then
    echo "     ✅ $1"
  else
    echo "     ❌ $1 —— 未被拦截"; LAYERS_OK=0
  fi
}
probe_blocked "第一层 app_rw DELETE  " app_rw "DELETE FROM audit_log;"
probe_blocked "第二层 属主 TRUNCATE  " app_migrator "TRUNCATE audit_log;"
probe_blocked "第三层 属主 ALTER     " app_migrator "ALTER TABLE audit_log DISABLE TRIGGER trg_audit_no_truncate;"

echo ""
if [ "$EXPECTED" = "$ACTUAL" ] && [ "$LAYERS_OK" = "1" ]; then
  echo "✅ 判据 3 PASS —— 行数一致，且三层防护在新实例上复现"
else
  [ "$EXPECTED" = "$ACTUAL" ] || echo "❌ 行数不一致"
  [ "$LAYERS_OK" = "1" ]      || echo "❌ 防篡改层未完整恢复"
  echo "❌ 判据 3 FAIL"
  exit 1
fi
