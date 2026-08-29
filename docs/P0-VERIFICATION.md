# P0 前置验证周 · 验证结果

| 项 | 值 |
|---|---|
| 执行日期 | 2026-08-29 |
| 验证环境 | 本机 macOS（Mac16,10 / 10 核 / 16GB）+ Docker |
| 关联 | [设计文档 v1.9](superpowers/specs/2026-08-28-enterprise-ai-poc-design.md) · [版本矩阵](TECH-STACK-VERSIONS.md) · [评审报告](REVIEW-PANEL-2026-08-28.md) |

---

## 结果总览

| # | 验证项 | 结果 | 影响 |
|---|---|---|---|
| 1 | **R1 型号核实** | 🔴 **发现重大错误** | **设计前八版用错了型号**，见 §1 |
| 2 | asyncpg 0.31.0 × PG 18.6 | ✅ 通过 | PG 18.6 决策成立，无需换驱动 |
| 3 | pgvector 0.8.6 + HNSW | ✅ 通过 | 无需退回 PG 17 |
| 4 | 四维元数据过滤 + 向量检索 | ✅ 通过，且**优于预期** | 见 §3 |
| 5 | langgraph-checkpoint-postgres 3.1.2 | ✅ 通过 | — |
| 6 | psycopg3 async 退路 | ✅ 可用 | 退路仍在 |
| 7 | 前端栈（React 19 + antd 6 + Vite 8 + TS 7） | ✅ 通过 | **TS 7 不必回退** |
| 8 | Dify 单实例整合 + 容器内存实测 | 🟡 **部分完成** | 单实例达成，但 PG 仍归 Dify compose 管，见 §6 |
| 9 | RAG spike（引用可溯源） | ⏸ 未执行 | 依赖 Dify |
| 10 | AutoDL + vLLM（含 tool_calls） | ⏸ **转待办** | 用户决定优先走厂商 API，M1/M2 延后 |
| 11 | 托管档 M3 / M4 | ✅ **已定** | AutoDL.Art `Qwen3.5-397B-A17B` + DeepSeek 官方 `deepseek-v4-flash`，两个 key 用户已具备 |

---

## 1. 🔴 R1：立项指定的型号一直是对的，设计用错了

**立项 brief 写的是「Qwen3.8 系列 27B」。设计文档 v1.0 断言该型号不存在**，理由是知识截止于 2026-05，改用了 Qwen3-32B / Qwen3-30B-A3B。R1 风险登记了这一条并跨越了八个版本，直到 P0 才执行核实。

HuggingFace `Qwen` 官方账号实测存在：

```
Qwen/Qwen3.8-27B      ↓3,457,687      Qwen/Qwen3.8-27B-FP8   ↓3,974,725
Qwen/Qwen3.8-Flash-Next               Qwen/Qwen3.8-2.4T-A95B
```

同期还有 Qwen3.5 系列（0.8B / 2B / 27B / 35B-A3B / 122B-A10B），均在 2026-05 之后发布。

### 实测规格

| 变体 | 权重 | 分片 | 层数 | kv_heads | head_dim | 原生 ctx |
|---|---|---|---|---|---|---|
| `Qwen3.8-27B` | **51.7 GiB** | 18 | 64 | 4 | 256 | 262144 |
| `Qwen3.8-27B-FP8` | **28.7 GiB** | 66 | 64 | 4 | 256 | 262144 |

架构名 `Qwen3_5ForConditionalGeneration`（多模态族）。**vLLM 0.28.0 的 registry 已注册**，无需等版本更新：

```python
"Qwen3_5ForConditionalGeneration": ("qwen3_5", "Qwen3_5ForConditionalGeneration"),
"Qwen3_5MoeForConditionalGeneration": (...)
```

### 量化选型与 KV cache 实算

按实测架构参数：**KV/token = 64 层 × 4 kv_heads × 256 head_dim × 2(K,V) × 2B = 256 KiB**（BF16 KV），FP8 KV 减半为 128 KiB。

| 档位 | 量化 | 权重 | KV 预算 | BF16 KV 容量 | FP8 KV 容量 |
|---|---|---|---|---|---|
| **M1** 4090 24G | INT4 (W4A16) | ~14 GiB | 5.7 GiB | 23K token ❌ | **47K token ✅** |
| **M2** A100 80G | BF16 | 51.7 GiB | 16.6 GiB | **68K token ✅** | 136K token |

**10 并发 × 3.5K token（RAG prompt + 输出）= 35K token 需求。**

**M1 必须启用 `--kv-cache-dtype fp8`**，否则 23K 容量扛不住 10 并发。4090 是 Ada（sm89），**原生支持 FP8**。原设计文档完全未提这个参数。

### 为什么 M1 选 INT4 而非 FP4

FP4 与 INT4 **同为 4bit、体积相同**（约 14 GiB），但 **NVFP4/MXFP4 的硬件加速需要 Blackwell（sm100+）**，4090 是 Ada，原生支持 FP8 但不支持 FP4。在 4090 上跑 FP4 只能走反量化，无速度优势且内核成熟度更低。

生态数据印证：

```
cyankiwi/Qwen3.8-27B-AWQ-INT4                 ↓792,516
RedHatAI/Qwen3.8-27B-INT4                     ↓115,041
TelperionAI/Qwen3.8-27B-NVFP4-AWQ-AutoRound   ↓    591
YCWTG/Qwen3.8-27B-NVFP4A16-GPTQ               ↓    225
```

**选定 `RedHatAI/Qwen3.8-27B-INT4`**——RedHatAI 即 Neural Magic，vLLM 的主要维护方，其量化权重针对 vLLM 内核优化并经测试。这同时关闭了「社区量化可能落慢路径」的风险。

### 为什么 M2 选 BF16 而非 FP8

A100 是 Ampere（sm80），**无 FP8 张量核心**。加载 FP8 权重只能 weight-only 反量化回 BF16 计算——省显存不省算力，且引入量化损失。而 M2 的定位是「准确率上限基准」，必须无量化损失。

### 附带发现

Ollama 官方库已有 `qwen3.8:27b`（含 q4 / q8 / bf16 / mxfp8 / nvfp4 标签）。但 27B 的 q4 约 15 GB，本机 16 GB 统一内存跑不动，**M0 仍保持 `qwen3:8b`**。

---

## 2. PostgreSQL 18.6 全链路验证

容器 `pgvector/pgvector:pg18`，实际版本 **18.6 (Debian 18.6-1.pgdg12+2)**，pgvector 扩展 **0.8.6**。

### asyncpg 0.31.0（P0 最高优先级：唯一无证据项）

这是 PG 18 决策上唯一没有任何证据支持的组件——其 release notes 惯例是逐版本声明支持的 PG 大版本，v0.30.0 声明至 PostgreSQL 17，v0.31.0（PG 18 发布两个月后）未见 PG 18 声明。

**实测全部通过**：

| 测试 | 结果 |
|---|---|
| 连接与握手 | ✅ server_version 18.6 |
| 类型往返（10 种） | ✅ int / bigint / numeric→Decimal / text / timestamptz→datetime / jsonb / uuid→UUID / boolean / text[]→list / bytea→bytes |
| 预备语句（`$1::int + $2::int`） | ✅ |
| 显式事务 | ✅ |

**结论：无需切换驱动，`postgresql+asyncpg://` 保持不变。** psycopg3 async 退路同时验证可用。

### langgraph-checkpoint-postgres 3.1.2

`setup()` 成功建表，`alist()` 读取正常。建表清单：`checkpoints` / `checkpoint_blobs` / `checkpoint_writes` / `checkpoint_migrations`。

---

## 3. 向量检索路径：结果优于预期

2000 行 × 1024 维（bge-m3 输出维度）：

| 操作 | 耗时 |
|---|---|
| 插入 2000 行 | 280 ms |
| HNSW 索引构建 | 109 ms |
| 纯向量检索 top-5 | 0.17 ms（`Index Scan using chunks_hnsw` 确认走 HNSW） |
| **四维过滤 + 向量检索 top-5** | **0.73 ms** |

体积：表 152 kB，HNSW 索引 5792 kB，合计 17 MB。

### 关键发现：高选择性过滤下规划器绕开 HNSW，走精确路径

四维过滤查询的执行计划是：

```
Sort → Bitmap Heap Scan on chunks → Bitmap Index Scan on chunks_meta
Execution Time: 0.725 ms
```

**规划器没有使用 HNSW**，而是走 B-tree 元数据索引取出 125 行后**精确排序**。这是精确 k-NN，不是近似搜索——**召回率 100%，且比近似搜索更快**。

这实证了设计文档 v1.3 的预测。评审提出的「pgvector 的 HNSW 在高选择性过滤下召回衰减」在本项目规模下**不发生**，因为规划器自动绕开了 HNSW。

**版本矩阵 §9 记录的重估触发条件依然成立**：语料超过 10 万 chunk 时，过滤后子集大到精确扫描变贵，规划器才会转向 HNSW，届时召回衰减才成为真问题。

### 附带：造数据时踩到评审预言的坑

首次生成用 `bu = i%2`、`year = i%2`，两个维度完全相关，`BU-A` 永远配 2025，四维过滤查询返回 0 行。改用互质步长解相关后联合分布铺满（每个 (BU, 年度, 产品线) 组合 125 行）。

这正是数据架构评审「**数据分布未规格化——均匀随机生成产不出关键形态**」的现场实例，印证了 P2 必须交付 fixture 规格而非只写「每表 ≥100 条」。

---

## 4. 前端技术栈

Vite 官方 `react-ts` 模板脚手架，加装 antd 6 与 TypeScript 7：

```
react@19.2.8   react-dom@19.2.8   antd@6.6.2
typescript@7.0.2   vite@8.2.2   @vitejs/plugin-react@6.1.1
```

写了真实用到 antd 的组件（Modal + Table + Select + Tag + Space + `@ant-design/icons`）：

| 检查 | 结果 |
|---|---|
| `tsc --noEmit` | ✅ 退出码 0 |
| 生产构建 | ✅ 1.26s，3077 modules |
| 产物形态 | `dist/` 876K，仅 `index.html` + `assets/` |

**TypeScript 7 与 antd 6 + React 19 类型兼容，不必回退 5.x。** 产物为纯静态文件，**实证了「生产环境无 Node 运行时」的结论**。

⚠️ **需在 P3 处理**：bundle 866 kB（gzip 274 kB）超过 500 kB 警戒线，antd 全量引入所致，需做 code splitting。

---

## 5. 🔴 M3（阿里云百炼）阻塞

调用 OpenAI 兼容端点 `/compatible-mode/v1/models` **返回 0 个模型**。

`.env` 中的 `DASHSCOPE_API_KEY` **仅 5 个字符**（未打印值）。真实的百炼 key 为 `sk-` 前缀、30+ 字符。

**M3 是核心对照组 M2 vs M3 的一半，当前无法验证。** 需要用户按宪法第七条自行注入真实 key：

```bash
echo 'DASHSCOPE_API_KEY=sk-你的真实key' >> .env
```

另需核实：**百炼是否已上架 qwen3.8-27b**。若未上架，M2 vs M3 的「同权重同规格」前提不成立，需改为对比可得的最接近型号并在报告中声明差异。

---

## 6. 待办与阻塞

| # | 事项 | 阻塞原因 | 需要谁 |
|---|---|---|---|
| B1 | AutoDL 实例与 SSH 凭据 | 无实例 | **用户** |
| B2 | 百炼真实 API Key | `.env` 中为占位值 | **用户** |
| B3 | 百炼是否上架 qwen3.8-27b | 依赖 B2 | 待 B2 后核实 |
| B4 | Dify 指向外部 PG + 13 容器内存实测 | 可执行，未做 | 下一步 |
| B5 | RAG spike（引用可溯源） | 依赖 B4 | 下一步 |

**验证容器 `poc-pg18` 仍在运行**（端口 55432）。不再需要时：`docker rm -f poc-pg18`


---

## 7. P0-5 · Dify 单实例整合实测（2026-08-29）

### 结果

| 目标 | 结果 |
|---|---|
| 消除多余的 PostgreSQL 实例（R15） | ✅ **Dify 侧 PostgreSQL 实例数 = 1** |
| 消除 weaviate | ✅ **0 个** |
| `dify` 库启用 pgvector | ✅ **vector 0.8.6 已装** |
| 数据完好 | ✅ `apps=1` 与改动前一致，三个库齐全 |
| `COMPOSE_PROFILES` 字面量覆写 | ✅ 插值已去除 |
| **Dify 指向真正的外部 PG** | 🟡 **未达成**，见「遗留」 |
| 容器内存实测 | ✅ **14 个容器合计 2.13 GiB** |

改动前完整备份于 `/Users/punjab/Documents/Workspace/dify/backup-20260829-165824/`（`.env`、`docker-compose.yaml`、两个库的 `pg_dump -Fc`）。

### R15 完全证实

`.env` 实测：

```
COMPOSE_PROFILES=${VECTOR_STORE:-weaviate},${DB_TYPE:-postgresql},collaboration
```

确为**插值派生**。设 `VECTOR_STORE=pgvector` 会使 `pgvector` 自动回到 profiles 并拉起第二个 PostgreSQL 容器。已覆写为字面量 `postgresql,collaboration`。

**时机极佳**：改动前知识库 0 个、文档 0 个，切换向量后端**零损失**，无需重新索引。

### 两处此前未核实的变量名，一对一错

| R15 / 版本矩阵 §6 所写 | 实测（源码 `PGVectorConfig`） |
|---|---|
| `PGVECTOR_HOST` / `PGVECTOR_PORT` / `PGVECTOR_DATABASE` | ✅ 名字正确 |
| （未提及端口默认值） | 🔴 **`PGVECTOR_PORT` 源码默认 5433**，而目标实例在容器网内是 **5432**，不显式设则连不上 |

权威变量名（`api/configs/middleware/vdb/pgvector_config.py`）：`PGVECTOR_HOST` · `PGVECTOR_PORT`(默认5433) · `PGVECTOR_USER` · `PGVECTOR_PASSWORD` · `PGVECTOR_DATABASE` · `PGVECTOR_MIN_CONNECTION` · `PGVECTOR_MAX_CONNECTION` · `PGVECTOR_PG_BIGM`。

注意是 `PGVECTOR_USER` 而非 `PGVECTOR_USERNAME`。

### 另一处坑：官方 postgres 镜像不含 pgvector

用户原先将 Dify 的 `db_postgres` 改为 `postgres:18.6`。该官方镜像**不含 pgvector 扩展**——`pg_available_extensions` 中查不到 `vector`。已改为 `pgvector/pgvector:pg18`（数据为 bind mount，PG 18→18 大版本一致，数据兼容）。

### 换镜像引入 collation 版本不匹配（已修复）

```
database "dify" has a collation version mismatch
created using collation version 2.41, but the OS provides version 2.36
```

原因：`postgres:18.6` 基于 **Debian 13**（pgdg13），`pgvector/pgvector:pg18` 基于 **Debian 12**（pgdg12），glibc 不同导致排序规则版本回退。会影响文本列 B-tree 索引的正确性。

已对 `postgres` / `dify` / `dify_plugin` 三库执行 `REINDEX DATABASE` + `ALTER DATABASE ... REFRESH COLLATION VERSION`，警告消失。当时数据量极小，成本接近零。

> **通用教训**：跨镜像基础发行版更换 PostgreSQL 容器时，即使 PG 大版本相同，也须检查并修复 collation 版本。数据量大时 `REINDEX` 代价很高，应在数据积累前完成迁移。

### 内存：评审的担忧被证伪

| 来源 | 估算 | 实测 |
|---|---|---|
| 设计文档 v1.1 | Dify 约 3.5 GiB | — |
| DevOps 评审重估 | 3.6–7.0 GiB，中位 **5 GiB** | — |
| **实测（14 容器）** | — | **2.13 GiB** |

分项（重启后）：`api` 415 MiB · `api_websocket` 407 MiB · `worker_beat` 397 MiB · `worker` 399 MiB · `web` 118 MiB · `agent_backend` 111 MiB · `plugin_daemon` 82 MiB · `sandbox` 66 MiB · `db_postgres` 46 MiB · 其余各 ≤38 MiB。

Docker Desktop 分配 **7.75 GiB**，实际占用 2.13 GiB。

**因此评审建议的内存优化（`CELERY_WORKER_AMOUNT=1`、去掉 `collaboration` profile）本期不做**——余量充足，改动只会增加与官方部署的偏离。「16 GB 扛不住 Dify」这一自 v1.1 起悬挂多版的担忧可以关闭。

### 🟡 遗留：PG 仍归 Dify compose 管，且不对宿主暴露

当前形态是「Dify 自带的 `db_postgres` 升级为 pgvector 并吞下向量存储」，**不是设计所要求的「外部唯一权威实例」**。两个后果：

| 问题 | 影响 |
|---|---|
| `db_postgres` **无 `ports` 映射**，宿主 `127.0.0.1:5432` 不通 | 跑在宿主机上的 FastAPI **连不上**，P1 直接受阻 |
| PG 生命周期由 Dify 的 compose 掌管 | 在 Dify 目录执行 `docker compose down -v` 会**连业务库与审计日志一并销毁**——正是 DevOps 评审警告的那个杀手，现在风险更实 |

**两条处置路径**：

| | 做法 | 得 | 失 |
|---|---|---|---|
| **A** | 给 `db_postgres` 加 `ports: 5432:5432` | 改动最小，宿主可达 | 生命周期仍与 Dify compose 耦合；偏离官方 compose |
| **B** | 起独立 PG 容器，Dify 经 `host.docker.internal` 指向它 | 符合设计原意，生命周期解耦，`down -v` 不再危及业务数据 | 需迁移现有 `dify` / `dify_plugin` 两库（当前数据极小，成本近零） |

**推荐 B**。现在数据量微不足道（`apps=1`、知识库 0），迁移成本几乎为零；等 P2 灌入业务数据、P3 建好知识库后再迁，代价会高一个量级。
