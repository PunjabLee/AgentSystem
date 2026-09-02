#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# P1.5.2 备份
#
# 四件套，缺一件恢复演练就过不了：
#   ① agentsystem 库   pg_dump -Fc（自定义格式，支持并行恢复与选择性还原）
#   ② dify 库          同上 —— 知识库的切块与向量都在里面，丢了要重灌全部文档
#   ③ 集群角色         pg_dumpall --roles-only —— pg_dump **不含**角色定义，
#                      只恢复库会得到一堆「role does not exist」
#   ④ Dify storage     上传的原始文件；库里只有切块，重灌需要原件
#
# 用法：make backup  或  bash scripts/backup.sh [输出目录]
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

CONTAINER="${PG_CONTAINER:-docker-db_postgres-1}"
DIFY_VOLUMES="${DIFY_VOLUMES:-/Users/punjab/Documents/Workspace/dify/docker/volumes}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${1:-backups}/$STAMP"

mkdir -p "$OUT"
echo "备份目录: $OUT"

echo "── ① agentsystem 库 ──"
docker exec -i "$CONTAINER" pg_dump -U postgres -Fc -d agentsystem > "$OUT/agentsystem.dump"

echo "── ② dify 库 ──"
docker exec -i "$CONTAINER" pg_dump -U postgres -Fc -d dify > "$OUT/dify.dump"

echo "── ③ 集群角色 ──"
# pg_dump 不含角色定义。只备份库、不备份角色，恢复时会得到一堆
# 「role "app_rw" does not exist」，而那时才发现就太晚了。
docker exec -i "$CONTAINER" pg_dumpall -U postgres --roles-only > "$OUT/roles.sql"

echo "── ④ Dify storage ──"
if [ -d "$DIFY_VOLUMES/app/storage" ]; then
  tar -czf "$OUT/dify-storage.tar.gz" -C "$DIFY_VOLUMES/app" storage
else
  echo "  ⚠️  未找到 $DIFY_VOLUMES/app/storage，跳过（设 DIFY_VOLUMES 指定路径）"
fi

# 记录源端行数，供恢复演练比对。没有它，"恢复成功"只能靠肉眼看没报错。
docker exec -i "$CONTAINER" psql -U postgres -d agentsystem -tAq -c \
  "SELECT 'audit_log='||(SELECT count(*) FROM audit_log)||
          ' write_intent='||(SELECT count(*) FROM write_intent)" > "$OUT/rowcounts.txt"

{
  echo "backup_at=$STAMP"
  echo "pg_version=$(docker exec -i "$CONTAINER" psql -U postgres -tAq -c 'SHOW server_version')"
  echo "alembic_head=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && uv run alembic heads 2>/dev/null | head -1)"
} > "$OUT/manifest.txt"

echo ""
echo "✅ 完成"
ls -lh "$OUT" | tail -n +2 | awk '{printf "   %-24s %s\n", $9, $5}'
echo "   源端行数: $(cat "$OUT/rowcounts.txt")"
