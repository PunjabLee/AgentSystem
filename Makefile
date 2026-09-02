# P1 详细设计 §7 的目标契约。
#
# 未实现的目标一律**显式失败**，不留空壳 —— 一个静默成功的 make backup
# 比没有 make backup 危险得多。

.PHONY: help chat test lint fmt check migrate migrate-audit mint-tokens \
        audit-tail dev backup restore-drill p1-exit

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

# ── 尚未实现（P1.5 / P2）──────────────────────────────────────
dev:
	@echo "❌ make dev 待 P2.3.1 的 FastAPI 骨架落地后实现" && exit 1

backup:
	@echo "❌ make backup 属 P1.5.2，尚未实现" && exit 1

restore-drill:
	@echo "❌ make restore-drill 属 P1.5.3，尚未实现" && exit 1

p1-exit:
	@echo "❌ make p1-exit 属 P1.6，待判据 2/3 的实现落地后接线" && exit 1
