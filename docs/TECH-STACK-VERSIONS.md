# 技术栈版本矩阵 · Tech Stack Version Matrix

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 核实日期 | **2026-08-28** |
| 核实方式 | 直接查询 PyPI JSON API、npm registry、GitHub Releases API、endoflife.date API、Dify 源码 |
| 关联 | [设计文档](superpowers/specs/2026-08-28-enterprise-ai-poc-design.md) v1.6 · [项目宪法](CONSTITUTION.md) |

> 本文档记录**核实日期当天**各组件的实际版本，非凭记忆填写。版本会持续变动，每阶段 converge 检查时应重新核实并更新本文档。

---

## 1. 结论摘要

| # | 结论 | 等级 |
|---|---|---|
| 1 | Dify **确认支持 pgvector**（`VectorType.PGVECTOR`），Qdrant / Milvus 同在支持列表，切换验证可行 | ✅ 关闭 R10 |
| 2 | **Dify 默认 compose 会额外起一个 PostgreSQL 容器给 pgvector**，若不干预将出现三个 PG 实例，彻底违背选型初衷 | ⚠️ 必须处理 |
| 3 | Python **3.14 全部关键依赖均有预编译轮子**，本机 3.14.6 可直接使用 | ✅ 无冲突 |
| 4 | Node **24.18 已装且为 LTS**，满足 Vite 8 的 `>=22.12` 要求 | ✅ 无冲突 |
| 5 | **Dify 2.0 仍处 beta**，8 周周期内可能转正——本项目锁定 1.17.0 不追 | ⚠️ 已登记风险 |
| 6 | **TypeScript 7 是 Go 原生重写版**，绿地项目无迁移负担但周边工具可能滞后 | ⚠️ P3 观察 |
| 7 | Ubuntu **25.04 / 25.10 均已 EOL**，AutoDL 选镜像时不可选用 | ⚠️ 选型约束 |

---

## 2. 运行时与操作系统

| 位置 | 组件 | 推荐版本 | EOL | 说明 |
|---|---|---|---|---|
| 本机 | macOS | Darwin 25.4.0（Mac16,10, 10 核 / 16GB） | — | 开发机，现状 |
| 本机 | **Python** | **3.14.6**（已装） | 2030-10-31 | 全部关键依赖有 cp314 轮子，见 §3 |
| 本机 | **Node.js** | **24.18**（已装，LTS） | 2028-04-30 | 满足 Vite 8 的 `^20.19 \|\| >=22.12` |
| 本机 | Docker | 29.5.3（已装） | — | 需启动 Docker Desktop |
| AutoDL | **Ubuntu** | **24.04 LTS**（22.04 亦可） | 2029-05-31 | 见下方约束 |
| AutoDL | Python | 沿用镜像自带（通常 3.10–3.12） | — | 推理侧独立环境，无需与本机一致 |

**Node 版本说明**：Node 25 已于 2026-06-01 EOL（奇数版本生命周期短），不可选用；Node 26（EOL 2029-04-30）更新但本机 24.18 已是 LTS 且满足全部要求，无更换必要。

**Ubuntu 版本约束**：25.04（EOL 2026-01-17）与 25.10（EOL 2026-07-01）**均已过期**，AutoDL 若提供此类镜像不可选用。26.04 LTS（EOL 2031-04-30）过新，CUDA 驱动与深度学习镜像生态尚未跟上。**选 24.04 LTS**。

---

## 3. 应用侧 Python 依赖（本机）

核实日期各包最新正式版及其 Python 版本要求：

| 包 | 最新版 | `requires_python` | 3.14 轮子 |
|---|---|---|---|
| fastapi | 0.141.1 | `>=3.10` | 纯 Python |
| uvicorn | 0.52.4 | `>=3.10` | 纯 Python |
| sse-starlette | 3.4.8 | `>=3.10` | 纯 Python |
| langgraph | 1.2.11 | `>=3.10` | 纯 Python |
| langgraph-checkpoint-postgres | 3.1.2 | `>=3.10` | 纯 Python |
| langchain-core | 1.6.1 | `>=3.10,<4.0` | 纯 Python |
| pydantic | 2.13.4 | `>=3.9` | ✅ `pydantic-core` 2.48.0 有 cp314 |
| sqlalchemy | 2.0.52 | `>=3.7` | ✅ `greenlet` 3.5.5 有 cp314 |
| alembic | 1.19.1 | `>=3.10` | 纯 Python |
| asyncpg | 0.31.0 | `>=3.9` | ✅ cp314 |
| psycopg | 3.3.4 | `>=3.10` | ✅ `psycopg-binary` 有 cp314 |
| pgvector（Python 客户端） | 0.5.0 | `>=3.10` | 纯 Python |
| httpx | 0.28.1 | `>=3.8` | 纯 Python |
| openai | 3.5.0 | `>=3.10` | 纯 Python |
| tiktoken | 0.14.0 | — | ✅ cp314 |
| playwright | 1.62.0 | `>=3.10` | ✅ `py3-none-<平台>` 轮子，ABI 无关 |
| pytest | 9.1.1 | `>=3.10` | 纯 Python |
| pytest-asyncio | 1.4.0 | `>=3.10` | 纯 Python |
| ruff | 0.16.5 | `>=3.7` | ✅ `py3-none-<平台>` 轮子，ABI 无关 |

**结论**：Python 3.14.6 **无冲突**，可直接使用本机已装版本。

**退路**：若 P1 安装时遇到任何**传递依赖**缺 cp314 轮子（本次仅核实直接依赖），退回 Python 3.13（EOL 2029-10-31）。以 `uv` 管理虚拟环境，切换成本约十分钟。

**锁定策略**：`pyproject.toml` 声明范围 + `uv.lock` 锁定精确版本，`uv.lock` **提交进 git**。

---

## 4. 前端依赖

| 包 | 最新版 | engines / peerDeps |
|---|---|---|
| react / react-dom | **19.2.8** | — |
| antd | **6.6.2** | `react >=18.0.0` |
| @ant-design/icons | 6.3.2 | `node >=8` |
| vite | **8.2.2** | `node ^20.19.0 \|\| >=22.12.0` |
| @vitejs/plugin-react | 6.1.1 | `node ^20.19.0 \|\| >=22.12.0` |
| typescript | **7.0.2** | `node >=16.20.0` |

**已排除**：`@ant-design/pro-components`（最新 2.8.10，发布于 2025-07-17，停更逾一年，peerDeps 仅 `antd ^4 || ^5`，**不支持 antd 6**）。所需组件均在 antd 主包内。

**TypeScript 7 说明**：TS 7 是 Go 语言原生重写版本，编译性能大幅提升。绿地项目无迁移负担，但周边生态（ESLint 插件、类型定义包）可能存在滞后。**列为 P3 观察项**：若前端工具链出现兼容问题，回退 TypeScript 5.x 的成本很低。

**锁定策略**：`package-lock.json` 提交进 git。

---

## 5. 基础设施组件

| 组件 | 推荐版本 | 最新版（核实日） | 说明 |
|---|---|---|---|
| **PostgreSQL** | **17** | 18（EOL 2030-11-14） | 选 17（EOL 2029-11-08）：云 RDS 普遍支持，pgvector 0.8.x 兼容成熟。若目标 RDS 已提供 18 则可用 18 |
| **pgvector 扩展** | **0.8.6** | v0.8.6 | 该项目以 git tag 发布，无 GitHub Release |
| **Dify** | **1.17.0** | 1.17.0（2026-08-25） | 2.0.0-beta.2 在测，本项目**锁定 1.17.0 不追 2.0** |
| **vLLM** | **0.28.0** | 0.28.0（2026-08-26） | `requires_python: >=3.10,<3.15` |
| **Ollama** | **0.33.1** | 0.33.1（2026-08-26） | 本机已装，恰为最新 |
| PyTorch（vLLM 依赖） | 随 vLLM 版本 | 2.13.0 | AutoDL 镜像自带，不单独指定 |

**生产数据库建议**：本机 PostgreSQL 仅用于 PoC 与开发。若 PoC 后进入实际使用，应迁移至云 RDS 以获得稳定性、自动备份与高可用保障——RDS 需确认已启用 `pgvector` 扩展。

---

## 6. ⚠️ 关键部署陷阱：Dify 会额外起一个 PostgreSQL

**问题**：Dify 的 `docker/docker-compose.yaml` 中，向量库 `pgvector` 是一个**独立于 Dify 元数据库 `db_postgres` 的 service**，各自带 compose profile：

```
db_postgres   profiles: postgresql     ← Dify 元数据
pgvector      profiles: pgvector       ← 向量存储（另一个 PostgreSQL 容器）
```

若按默认方式部署，加上我们自己的业务库，将出现 **三个 PostgreSQL 实例**——这彻底违背了选用 pgvector 的核心理由（少一个组件、运维体系统一、SQL 可对账）。

**处置**：配置 Dify 指向**外部** PostgreSQL，并**不启用**这两个 compose profile。

| Dify 配置项 | 指向 |
|---|---|
| `DB_HOST` / `DB_PORT` / `DB_DATABASE` | 我们的 PostgreSQL 实例 → `dify` 库 |
| `VECTOR_STORE` | `pgvector` |
| `PGVECTOR_HOST` / `PGVECTOR_PORT` / `PGVECTOR_DATABASE` | **同一实例** → `dify` 库 |
| `COMPOSE_PROFILES` | 移除 `postgresql` 与 `pgvector`，避免起容器 |

最终形态：**一个 PostgreSQL 实例**，`agentsystem` 库（业务 + 审计 + checkpointer）与 `dify` 库（Dify 元数据 + 向量），逻辑隔离。这与设计文档 §5.2 一致。

**此项为 P1 实施细节，必须写入部署文档并在 P1 converge 检查中验证。**

---

## 7. Dify 支持的向量后端（源码实况）

来自 `api/core/rag/datasource/vdb/vector_type.py`，共 32 种。与本项目相关的：

| 后端 | 枚举值 | 本项目角色 |
|---|---|---|
| **pgvector** | `pgvector` | ✅ **默认选用** |
| qdrant | `qdrant` | P5 切换验证目标 |
| milvus | `milvus` | 备选 |
| weaviate | `weaviate` | Dify 默认值，本项目不用 |
| chroma / opensearch / elasticsearch / tidb_vector / oceanbase / analyticdb 等 | — | 不涉及 |

`VECTOR_STORE` 环境变量切换 + 重新索引即可，应用侧经 `VectorStorePort` 不受影响。

---

## 8. 复核清单

每阶段 converge 检查时执行：

- [ ] 重新查询各 registry，更新本文档的版本与核实日期
- [ ] 确认 `uv.lock` 与 `package-lock.json` 已提交且可复现安装
- [ ] 确认 Dify 版本未被意外升级（尤其 2.0 转正后）
- [ ] 确认 PostgreSQL 实例数仍为一个（§6 陷阱未复发）
- [ ] 确认无传递依赖缺 Python 3.14 轮子
