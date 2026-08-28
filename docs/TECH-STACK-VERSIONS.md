# 技术栈版本矩阵 · Tech Stack Version Matrix

| 项 | 值 |
|---|---|
| 版本 | v1.1 |
| 核实日期 | **2026-08-28**（v1.1 补充 PostgreSQL 18 兼容性评估） |
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
| 8 | **PostgreSQL 改用 18.6**。pgvector 0.8.6 明确支持（CI 矩阵含 PG18 + 官方 `pg18` 镜像） | ✅ 硬证据 |
| 9 | **asyncpg 0.31.0 未声明支持 PG 18**，需 P1 实测；退路是切 psycopg3 async（改一行连接串） | ⚠️ P1 必测 |
| 10 | **Dify compose 钉 `postgres:15-alpine` / `pgvector:pg16`**，指向外部 PG 18 超出其测试矩阵，需 P1 实测 | ⚠️ P1 必测 |
| 11 | **阿里云 RDS 是否提供 PG 18 尚未查证**——若目标 RDS 仅到 17，本决策应推翻 | ❓ 待用户确认 |

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
| **PostgreSQL** | **18.6** | 18.6（2025-09-25 发布，EOL 2030-11-14） | 已迭代 6 个小版本。兼容性评估见 §9 |
| **pgvector 扩展** | **0.8.6** | v0.8.6 | 该项目以 git tag 发布，无 GitHub Release |
| **Dify** | **1.17.0** | 1.17.0（2026-08-25） | 2.0.0-beta.2 在测，本项目**锁定 1.17.0 不追 2.0** |
| **vLLM** | **0.28.0** | 0.28.0（2026-08-26） | `requires_python: >=3.10,<3.15` |
| **Ollama** | **0.33.1** | 0.33.1（2026-08-26） | 本机已装，恰为最新 |
| PyTorch（vLLM 依赖） | 随 vLLM 版本 | 2.13.0 | AutoDL 镜像自带，不单独指定 |

**生产数据库建议**：本机 PostgreSQL 仅用于 PoC 与开发。若 PoC 后进入实际使用，应迁移至云 RDS 以获得稳定性、自动备份与高可用保障——RDS 需确认已启用 `pgvector` 扩展。

---

## 9. PostgreSQL 18 兼容性评估

**决策**：由 17 改用 **18.6**（发布 2025-09-25，EOL 2030-11-14，已迭代 6 个小版本）。

### 逐组件兼容性

| 组件 | 结论 | 证据 |
|---|---|---|
| **pgvector 0.8.6** | ✅ **明确支持** | CI 工作流矩阵含 `postgres-version: 18`；官方镜像有 `0.8.6-pg18`、`pg18-trixie`、`pg18-bookworm` |
| psycopg 3.3.4 | ✅ 低风险 | 基于 libpq，客户端连新版服务端为标准做法 |
| SQLAlchemy 2.0.52 | ✅ 低风险 | 方言层，不依赖 PG 具体版本特性 |
| alembic 1.19.1 | ✅ 低风险 | 迁移 DDL 在 PG 18 向后兼容 |
| **asyncpg 0.31.0** | ⚠️ **未声明，P1 必测** | 见下 |
| **Dify 1.17.0** | 🟡 **启动路径已实测通过**，向量路径待测 | 用户 2026-08-28 于本机 Mac mini 实测：Dify 1.17.0 compose 的 PG 调整为 18.6 后成功启动、服务运行正常。见下 |

### asyncpg：证据边界要说清楚

asyncpg **自行实现 PostgreSQL 线协议**（不走 libpq），因此对 PG 大版本敏感。其惯例是每支持一个新 PG 大版本即在 release notes 明确声明：

| 版本 | 日期 | 声明 |
|---|---|---|
| v0.29.0 | 2023-11-05 | "Python 3.12 and PostgreSQL 16 support" |
| v0.30.0 | 2024-10-20 | "Support Python 3.13 and PostgreSQL 17" |
| **v0.31.0** | **2025-11-24** | **未见 PostgreSQL 18 声明**（PG 18 已于两个月前发布） |

**本次仅核实 release notes 文本，未实测连接。** 此证据不足以断定不兼容，也不足以断定兼容。

**退路（成本极低）**：技术栈中已同时存在 psycopg 3.3.4（`langgraph-checkpoint-postgres` 本就依赖它），且 psycopg3 原生支持 async。若 asyncpg 在 PG 18 上失败，连接串由 `postgresql+asyncpg://` 改为 `postgresql+psycopg://` 即可，psycopg3 基于 libpq，对新版 PG 兼容性天然更好。

### Dify：启动路径已由实测证实，向量路径与外部实例拓扑仍待验

Dify 1.17.0 的 `docker-compose.yaml` 原钉死 `postgres:15-alpine`（元数据库）与 `pgvector/pgvector:pg16`（向量库），指向 PG 18 属于其未测试组合。

**已证实（用户实测，2026-08-28，本机 Mac mini）**：将 Dify 1.17.0 compose 的 PostgreSQL 调整为 **18.6** 后，**成功启动且服务运行正常**。

这一条证据的分量不小——Dify 启动即意味着其 **Alembic 迁移在 PG 18.6 上全部执行成功**，而"迁移能否跑通"正是本项风险中最难预判、失败后代价最大的一环。原判断「风险中等偏低」得到证实。

**但该实测尚未覆盖三件事，不可外推**：

| 未覆盖 | 为什么重要 |
|---|---|
| **pgvector 向量路径** | 启动只证明元数据库可用。建知识库 → 索引文档 → 检索出结果这条链路才会真正调用 `vector` 类型与 HNSW 索引，是 PG 18 上最可能出问题的部分 |
| **外部单实例拓扑（R15）** | 实测改的是 Dify **自带**的 PG 容器；目标形态是 Dify 指向**外部唯一权威实例**（`DB_HOST` + `PGVECTOR_HOST`），连接方式与权限模型均不同 |
| **本项目应用侧的驱动** | Dify 走 SQLAlchemy + psycopg（libpq），与本项目主用的 **asyncpg** 是两套完全不同的实现，其兼容性未受此实测影响 |

因此 asyncpg 仍是 PG 18 决策上**唯一未获任何证据支持**的组件，是 P1 验证的首要目标。

### 收益的诚实评估

PG 18 的新特性（异步 I/O、UUIDv7、虚拟生成列、B-tree skip scan）**对本 PoC 基本用不上**——几千 chunk 向量与 100 条/表业务数据的规模下，17 与 18 的性能差异不可观测。**真实收益仅为 EOL 多一年**（2030-11-14 vs 2029-11-08）。

### 因此：验证前置到 P1 第一天

现在验证的沉没成本为零，失败即退回 PG 17；后期再迁移的成本远高于此刻验证。验证清单（原估 0.5 人天，因 Dify 启动路径已由用户实测关闭，**余量约 0.35 人天**，已含在 P1 内）：

- [x] ~~Dify 1.17.0 在 PG 18.6 上跑通 Alembic 迁移并正常启动~~ ✅ **用户已实测通过（2026-08-28）**
- [ ] **asyncpg 0.31.0 连接 PG 18.6**，跑基础 CRUD 与类型往返 ← **最高优先级，唯一无证据项**；失败则切 psycopg3 async
- [ ] 起 PG 18.6，装 pgvector 0.8.6，建 HNSW 索引并跑通向量检索
- [ ] Dify 建知识库 → 索引一份文档 → 检索出结果（走通向量路径，补齐实测缺口）
- [ ] Dify 指向**外部**唯一权威 PG 实例（非自带容器），验证 R15 的拓扑与只读权限模型
- [ ] `langgraph-checkpoint-postgres` 3.1.2 在 PG 18 上建表并读写检查点
- [ ] 失败处置：**asyncpg 失败 → 切 psycopg3 async，PG 保持 18.6**（成本极低）；**pgvector 或 Dify 向量路径失败 → 整体退回 PG 17**（版本矩阵与设计文档同步回滚）

> ❓ **需用户确认的前置条件**：**阿里云 RDS 是否已提供 PostgreSQL 18 未经查证**。若目标 RDS 仅支持到 17，则以 18 开发反而制造迁移障碍，本决策应予推翻。已登记为待决事项 Q5。

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
