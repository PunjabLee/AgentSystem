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
| 8 | Dify 指向外部 PG + 13 容器内存实测 | ⏸ 未执行 | 待办 |
| 9 | RAG spike（引用可溯源） | ⏸ 未执行 | 依赖 Dify |
| 10 | AutoDL + vLLM（含 tool_calls） | ⏸ **阻塞** | 需实例与凭据 |
| 11 | M3 百炼可用性 | 🔴 **阻塞** | `.env` 中的 key 无效，见 §5 |

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
