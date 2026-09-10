# P1 详细设计 §7 的目标契约。
#
# 未实现的目标一律**显式失败**，不留空壳 —— 一个静默成功的 make backup
# 比没有 make backup 危险得多。

.PHONY: help chat test lint fmt check audit-health seed seed-check migrate migrate-audit mint-tokens \
        audit-tail ollama-models dev backup restore-drill p1-exit

help:  ## 列出可用目标
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ── 开发 ──────────────────────────────────────────────────────
fmt:  ## 格式化
	uv run ruff format .

lint:  ## 静态检查（宪法第十一条的机械落地物）
	uv run ruff check .

test:  ## 跑测试（不含 LLM 调用，见 P1.5.1）
	uv run pytest

check: lint test  ## lint + test，提交前跑这个

seed:  ## 清空并重灌模拟数据 + 跑断言校验（P2.2.x）
	@uv run python scripts/seed.py

seed-check:  ## 只跑断言校验，不动数据
	@uv run python scripts/seed.py --check

audit-health:  ## 查有 attempt 无 outcome 的写操作（两段式的自检）
	@uv run python -c "\
import asyncio; from agentsystem.audit import find_hanging_attempts; \
rows = asyncio.run(find_hanging_attempts()); \
print('  ✅ 无悬挂 attempt') if not rows else \
[print(f'  🔴 {r.describe()}') for r in rows]"
	@uv run ruff format --check .

# ── 数据库 ────────────────────────────────────────────────────
migrate:  ## 常规迁移（不含 audit_log 结构变更）
	uv run alembic upgrade head

migrate-audit:  ## audit_log 结构变更：解锁 → 迁移 → 重新上锁
	bash scripts/migrate_audit_schema.sh

# ── 配置 ──────────────────────────────────────────────────────
mint-tokens:  ## 幂等铸造演示用户令牌（明文进 .env，哈希进 users.yaml）
	uv run python scripts/mint_user_tokens.py

# ── 模型 ──────────────────────────────────────────────────────
chat:  ## 模型冒烟；TIER=M4 可指定单档，缺省跑全部可用档
	uv run python scripts/chat.py $(if $(TIER),--tier $(TIER),)

ollama-models:  ## 从 ollama/*.Modelfile 重建 M0 的两个本地模型
	ollama create poc-chat  -f ollama/poc-chat.Modelfile
	ollama create poc-embed -f ollama/poc-embed.Modelfile
	@ollama list | grep -E '^poc-'

# ── 审计 ──────────────────────────────────────────────────────
N ?= 20
audit-tail:  ## 尾随最近 N 条审计，按 trace_id 分组显示 attempt/outcome 配对
	@docker exec -i $${PG_CONTAINER:-docker-db_postgres-1} psql -U postgres -d agentsystem -P pager=off -c "\
	  SELECT trace_id, \
	         string_agg(phase || coalesce('/' || status, ''), ' → ' ORDER BY id) AS 配对, \
	         min(user_id) AS 操作者, min(action_type) AS 类型, \
	         min(tool_name) AS 工具, max(error_code) AS 错误码, \
	         max(occurred_at) AS 时间 \
	    FROM (SELECT * FROM audit_log ORDER BY id DESC LIMIT $(N)) t \
	   GROUP BY trace_id ORDER BY max(id) DESC;"

# ── 备份与恢复 ────────────────────────────────────────────────
backup:  ## 四件套备份：两库 + 集群角色 + Dify storage
	bash scripts/backup.sh

restore-drill:  ## 恢复到干净容器并比对行数与三层防护（判据 3）
	bash scripts/restore_drill.sh

# ── 出口判据 ──────────────────────────────────────────────────
p1-exit:  ## 依次执行判据 1/2/3，输出 PASS/FAIL
	@bash scripts/p1_exit.sh

# ── 尚未实现 ──────────────────────────────────────────────────
dev:
	@echo "❌ make dev 待 P2.3.1 的 FastAPI 骨架落地后实现" && exit 1
