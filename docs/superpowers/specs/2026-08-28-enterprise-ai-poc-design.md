# 企业级智能问答与任务执行系统 PoC — 总体架构设计

## 0. 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.4（首轮评审修订） |
| 创建日期 | 2026-08-28 |
| 状态 | 待评审 |
| 业务域 | 织染（印染事业部）+ 瓷砖洁具（建陶卫浴事业部）双事业部制造集团 |
| 总工作量 | 31.5 人天 |

### 编号约定

为避免歧义，本文档使用以下互不重叠的编号前缀：

| 前缀 | 含义 | 范围 |
|---|---|---|
| `S1`–`S5` | 主线业务场景 | §3.3 |
| `M0`–`M4` | 模型部署档位 | §6.1 |
| `L1`–`L7` | LangGraph 承担的场景 | §7.3 |
| `D1`–`D12` | Dify 承担的场景 | §7.4 |
| `R1`–`R12` | 风险项 | §16 |
| `Q1`–`Q4` | 待决事项 | §17 |

### 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-08-28 | 初始基线。业务域由家电制造切换为织染 + 建陶卫浴；确认 Dify/LangGraph 十九项场景分解；GraphRAG 移出本期范围 |
| v1.1 | 2026-08-28 | 首轮评审修订。修正 §5.1 与 §8.1 关于向量库归属的自相矛盾（向量后端归 Dify 托管，默认改 Qdrant，新增 `RetrieverPort` 接口）；新增 FastAPI Gateway 请求分流层，LangGraph 不再是唯一入口；意图识别改三级路由以控延迟；新增 §10 前端实现规划与 §11 并发与性能设计。工期 30 → 31 人天 |
| v1.2 | 2026-08-28 | 新增 §8.4 向量数据纳管：明确写入权本期归 Dify 独占，排除双写反模式（双写会切断 Dify segment 元数据链路，直接击穿引用准确率指标）；`RetrieverPort` 扩展为 `VectorStorePort`，本期只实现读侧；新增 collection 命名规范做零成本环境隔离；《向量数据纳管服务设计》列为独立交付物。工期 31 → 31.5 人天 |
| v1.3 | 2026-08-28 | 向量后端由 Qdrant 改回 **pgvector**。v1.2 的论证有缺陷——推翻了「本机内存约束」这一条 pgvector 论据后即改选 Qdrant，未重新审视其余论据（少一个组件、运维体系统一、SQL 可达便于对账），而这些在本项目规模下均成立；Qdrant 的 payload 索引优势在几千 chunk 量级不发生。同时记录重估触发条件（>10 万 chunk 且高选择性过滤）。连带修正两处：PostgreSQL 由本机移至 GPU 服务器作为唯一权威实例（否则两侧各起一个实例将抵消「少一个组件」的优势）；蓝绿重建索引改在 Dify 知识库层完成而非向量库表层，避免落入双写反模式且做到 backend-agnostic。R11 补充 pgvector 下的权限隔离要求 |
| v1.4 | 2026-08-28 | 前端 UI 框架由 Ant Design 5 改为 **Ant Design 6**，React 由 18 改为 **19**（`react@latest` = 19.2.8）；核实后排除 ProComponents（`@ant-design/pro-components` 停更逾年且不支持 antd 6）。原选 v5 系依训练数据惯性而非当下判断；核实 npm registry 后确认 `latest` 已是 6.6.2（v6.x 共 33 个正式版），且绿地项目不存在留在 v5 的最大理由——迁移成本；antd 6 另原生支持 React 19，免去 v5 所需的补丁包。新增 §2.4：评估 github/spec-kit 后决定移植其 **constitution** 与 **converge** 两项机制而不引入完整工具链（避免与既有 superpowers 工作流形成双真相来源），新增 R12 |

---

## 1. 项目背景与目标

构建面向业务人员的企业级智能问答与任务执行系统，以「订单全生命周期」为主线，验证四项能力：

1. 自然语言完成业务查询与写操作
2. 静态知识（制度、SOP、FAQ）与动态业务数据（订单、库存、排产）的融合问答
3. LLM 与业务系统 API 的双向打通（读 + 写）
4. RAG、Workflow、RPA 三条技术路线的集成与协同

PoC 的产出用于为后续规模化落地提供选型与投入决策依据。

---

## 2. 范围界定

### 2.1 本期范围内

- 五个主线业务场景端到端可运行
- 五档模型部署矩阵（M0–M4）与统一抽象层
- Dify 十二项场景 + LangGraph 七项场景
- RPA 降级路径的**可执行实现**（Playwright）
- 评测体系与双部署对比报告
- **向量数据纳管：接口先行**（`VectorStorePort` 读侧实现 + collection 命名规范）
- 全套交付文档，含独立的《向量数据纳管服务设计》

### 2.2 本期明确排除

| 排除项 | 原因 |
|---|---|
| **GraphRAG / 知识图谱检索** | 语料仅 8 份文档、几千 chunk，够不着 GraphRAG 的收益区；查询本质是「年度×区域×产品」三维条件过滤，元数据过滤解得更准；且其答案生成自 community summary，溯源粒度粗，与「引用准确率 ≥85%」的验收指标直接冲突。经确认移出本期规划 |
| **影刀 / UiPath 可执行实现** | 两者均为 Windows-only，本项目开发机为 macOS，无法验证。本期仅交付流程设计文档，供后续 Windows 环境实施 |
| **图数据库（Neo4j 等）** | 业务多跳关系已存在于 PostgreSQL 外键中，SQL view 即可等价满足，引入图库纯增本无增益 |
| **自主协商式多 Agent（AutoGen 模式）** | Agent 间自由对话轮次不可控，token 成本与延迟不可预测、审计链路断裂，与「延迟指标」和「所有写操作留痕」两条硬要求正面冲突 |
| **真实 ERP / MES / WMS 对接** | PoC 阶段以模拟数据与模拟 API 验证链路可行性 |

### 2.3 后续可扩展清单

- **质量追溯图谱**：「B2409 缸布色差超标 → 反查所有受影响订单/客户/在途货/已开票」。在织染与建陶卫浴是真实高频痛点，深度多跳追溯场景下 Neo4j 才产生价值
- **向量数据纳管服务的实现**：本期只交付接口与设计，平台化实现需多个应用共用才验证得了价值（§8.4 演进路线）
- GraphRAG 作为检索路径的对照实验
- 影刀 RPA 在 Windows 环境的实施
- 真实业务系统对接与生产级权限体系

---

### 2.4 工程方法：借用 SDD 的两个机制，不引入 spec-kit 工具链

评估 [github/spec-kit](https://github.com/github/spec-kit)（Spec-Driven Development，2026-08 已发布 1.0.0）后的结论：**移植其两项机制，不引入完整工具链**。

**差距分析**——spec-kit 流水线与本项目已采用的 superpowers 工作流对照：

| spec-kit 阶段 | 本项目对应物 | 结论 |
|---|---|---|
| `/speckit-constitution` | 无 | **真缺口，需补** |
| `/speckit-specify` | `brainstorming` → 本设计文档 | 已覆盖 |
| `/speckit-plan` / `/speckit-tasks` | `writing-plans` 及其任务分解 | 已覆盖 |
| `/speckit-implement` | `executing-plans` | 已覆盖 |
| `/speckit-converge` | `verification-before-completion`（仅验证声称是否属实，不检测 spec↔代码漂移） | **部分缺口，需补** |

**不引入全套的三条理由**：

1. **双流程打架**——本项目 spec 位于 `docs/superpowers/specs/`，spec-kit 需 `.specify/` 与 `specs/NNN-feature/`，将产生两个真相来源
2. **模板不匹配**——spec-kit 的 spec 模板面向**功能级**（user story + 验收标准）；本文档是**程序级架构设计文档**，强行套用会丢失结构
3. **粒度不匹配**——spec-kit 按 feature 目录组织，本 PoC 需拆成十余个 feature 目录才能容纳

**移植的两项机制**：

| 机制 | 落地形式 | 成本 |
|---|---|---|
| **Constitution** | `docs/CONSTITUTION.md`，9 条不可协商原则，每模块动工前逐条核对 | 0.5 人天 |
| **Converge** | P1–P5 每阶段末增设收敛检查，对照本文档产出偏差清单并记录处置 | 每阶段 0.5 人天 |

**重新评估时机**：PoC 通过并进入规模化落地、团队转为多人与多 agent 并行开发时，标准化流水线的价值方才显现。列入 P5「后续规模化建议」。

---

## 3. 业务域建模

### 3.1 组织形态

双事业部制造集团：

- **BU-A 印染事业部**：坯布 → 前处理 → 染色 → 后整理 → 成品布
- **BU-B 建陶卫浴事业部**：
  - 瓷砖：压机 → 窑炉 → 抛光/施釉 → 分级
  - 洁具：注浆成型 → 施釉 → 烧成 → 检验

### 3.2 核心业务特征（贯穿全部设计的主线）

两个事业部共享一条**同构主线：批次色差 + 等级分选**。织染的「缸差」与瓷砖的「窑批色号差」在数据结构上完全同构，共同导致三个业务特征，这三个特征正是本 PoC 各项能力的真实需求来源：

| 业务特征 | 对 PoC 的支撑作用 |
|---|---|
| 库存必须按 **批次 / 色号 / 等级** 细分，不可按 SKU 汇总 | 「查库存」天然需要多轮追问（"要哪个色号？能接受跨缸吗？"），澄清能力具备真实必要性而非人为构造 |
| 订单常要求「同缸同色」「同窑批同色号」，否则客户拒收 | 「创建订单」的槽位比通用场景多一层约束，缺参追问更具说服力 |
| 排产需计算**换色 / 换规格的清洗调机损耗** | 「该订单会不会延误」具备真实计算逻辑，而非查询单个状态字段 |

### 3.3 五个主线场景

| 编号 | 场景 | 类型 | 关键难点 |
|---|---|---|---|
| S1 | 查询营销政策 | 读 | 年度 × 区域 × 产品三维过滤，多轮追问细化 |
| S2 | 查询库存 | 读 | 批次/色号/等级多维度，跨缸跨批意愿澄清 |
| S3 | 创建订单 | **写** | 槽位抽取、缺参追问、二次确认、幂等、审计 |
| S4 | 查询订单进展 | 读 | 订单号/客户/时间范围，生产与物流进度聚合 |
| S5 | 查询排产计划 | 读 | 与订单、库存联动分析，交期延误推理 |

---

## 4. 数据设计

### 4.1 业务表结构

三类业务数据，每表 ≥100 条模拟数据。

#### 4.1.1 订单（sales_order / sales_order_line）

```sql
CREATE TABLE sales_order (
  order_no        VARCHAR(32) PRIMARY KEY,      -- 订单号 SO-2026-000123
  bu_code         VARCHAR(8)  NOT NULL,         -- BU-A 印染 / BU-B 建陶卫浴
  customer_code   VARCHAR(32) NOT NULL,         -- 化名客户编码
  customer_name   VARCHAR(64) NOT NULL,         -- 化名，如「华东建材-A001」
  order_type      VARCHAR(16) NOT NULL,         -- 经销/工程/出口/样品
  order_date      DATE        NOT NULL,
  required_date   DATE        NOT NULL,         -- 客户要求交期
  promised_date   DATE,                         -- 承诺交期
  status          VARCHAR(16) NOT NULL,         -- 待评审/已确认/生产中/部分发货/已完成/已取消
  total_amount    NUMERIC(14,2),
  policy_code     VARCHAR(32),                  -- 适用营销政策
  created_by      VARCHAR(32),
  created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE sales_order_line (
  line_id         BIGSERIAL PRIMARY KEY,
  order_no        VARCHAR(32) REFERENCES sales_order(order_no),
  product_code    VARCHAR(32) NOT NULL,
  spec            VARCHAR(64) NOT NULL,         -- 门幅×克重 / 边长×厚度
  color_code      VARCHAR(32) NOT NULL,         -- 色号
  grade_required  VARCHAR(16) NOT NULL,         -- 优等品/一级品/合格品
  same_batch_req  BOOLEAN     NOT NULL,         -- 是否要求同缸同批（关键字段）
  qty             NUMERIC(14,3) NOT NULL,
  uom             VARCHAR(8)  NOT NULL,         -- 米 / 平方米 / 件
  unit_price      NUMERIC(12,4),
  line_status     VARCHAR(16)
);
```

#### 4.1.2 库存（inventory_batch）

```sql
CREATE TABLE inventory_batch (
  batch_id        BIGSERIAL PRIMARY KEY,
  warehouse_code  VARCHAR(16) NOT NULL,
  bu_code         VARCHAR(8)  NOT NULL,
  product_code    VARCHAR(32) NOT NULL,
  batch_no        VARCHAR(32) NOT NULL,         -- 染缸号 D2601-08 / 窑批号 K2603-15
  color_code      VARCHAR(32) NOT NULL,
  grade           VARCHAR(16) NOT NULL,         -- 优等品/一级品/合格品
  spec            VARCHAR(64) NOT NULL,
  delta_e         NUMERIC(5,2),                 -- 色差 ΔE，跨缸判定依据
  qty_available   NUMERIC(14,3) NOT NULL,
  qty_locked      NUMERIC(14,3) DEFAULT 0,
  inbound_date    DATE,
  qc_status       VARCHAR(16),                  -- 待检/合格/让步接收/不合格
  UNIQUE (warehouse_code, product_code, batch_no, color_code, grade)
);
```

#### 4.1.3 排产（production_plan）

```sql
CREATE TABLE production_plan (
  plan_no         VARCHAR(32) PRIMARY KEY,
  bu_code         VARCHAR(8)  NOT NULL,
  line_code       VARCHAR(32) NOT NULL,         -- 染缸编号 / 窑炉编号 / 成型线
  product_code    VARCHAR(32) NOT NULL,
  color_code      VARCHAR(32) NOT NULL,
  process_stage   VARCHAR(32) NOT NULL,         -- 前处理/染色/后整理 或 压机/窑炉/抛光/分级
  planned_qty     NUMERIC(14,3) NOT NULL,
  plan_start      TIMESTAMPTZ NOT NULL,
  plan_end        TIMESTAMPTZ NOT NULL,
  changeover_min  INTEGER DEFAULT 0,            -- 换色/换规格调机时长（分钟），延误推理关键
  status          VARCHAR(16) NOT NULL,         -- 待排/已排/生产中/已完成/暂停
  related_order   VARCHAR(32)                   -- 关联订单号
);
```

#### 4.1.4 审计表（audit_log）

```sql
CREATE TABLE audit_log (
  id              BIGSERIAL PRIMARY KEY,
  trace_id        VARCHAR(64) NOT NULL,
  session_id      VARCHAR(64) NOT NULL,
  user_id         VARCHAR(32) NOT NULL,
  action_type     VARCHAR(8)  NOT NULL,         -- read / write
  source          VARCHAR(16) NOT NULL,         -- langgraph / dify / rpa
  tool_name       VARCHAR(64),
  model_tier      VARCHAR(8),                   -- M0..M4，用于评测归因
  request_payload JSONB,                        -- 已脱敏
  response_payload JSONB,
  latency_ms      INTEGER,
  status          VARCHAR(16),                  -- success / failed / degraded
  confirm_token   VARCHAR(64),                  -- 写操作的二次确认令牌
  confirmed_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ DEFAULT now()
);
```

### 4.2 RAG 知识库文档（8 份）

| # | 文档 | 类型 | 支撑场景 |
|---|---|---|---|
| 1 | 2026 年度经销商营销政策（印染事业部） | 制度 | S1 |
| 2 | 2026 年度工程集采价格政策（建陶卫浴事业部） | 制度 | S1 |
| 3 | 产品手册：功能性面料系列 | 手册 | S1 S2 |
| 4 | 产品手册：岩板瓷砖 & 智能洁具系列 | 手册 | S1 S2 |
| 5 | 订单评审与交期承诺 SOP | SOP | S3 S5 |
| 6 | **色差与等级判定标准（含缸差/窑批差处理规则）** | 标准 | S2 S3 |
| 7 | 退换货与质量异议处理办法 | FAQ | 通用 |
| 8 | 库存管理与呆滞品清尾办法 | 制度 | S2 |

### 4.3 脱敏规则

- **客户名称**：全部化名，格式「区域 + 业态 - 序号」，如「华东建材-A001」「华南经销-B017」
- **价格**：使用相对系数（基准价 × 折扣系数），不出现真实成交价
- **联系人 / 电话 / 地址**：生成式假数据，不取自真实来源
- 若后续接入真实数据，脱敏须在数据出库环节完成，禁止在应用层做遮蔽

---

## 5. 总体技术架构

```
┌───────────────────────────────────────────────────────────────────┐
│  对话界面   React 19 + Vite + Ant Design 6                         │
│  档位切换 M0-M4 ▾ │ 引用来源卡片 │ 二次确认弹窗 │ 审计追溯视图        │
└────────────────────────────┬──────────────────────────────────────┘
                             │  SSE 流式 / REST
┌────────────────────────────▼──────────────────────────────────────┐
│  FastAPI Gateway         鉴权 · 限流 · 审计埋点 · 请求分流           │
└──────┬──────────────────────────────────────────────┬─────────────┘
       │ 有状态 / 事务 / 写操作                          │ 无状态只读
       │                                              │ (D5 FAQ · D6 报表)
┌──────▼──────────────────────────────────────────┐   │
│           LangGraph Agent 内核                   │   │
│                                                 │   │
│            ┌─────────────────┐                  │   │
│            │   Supervisor    │ 三级意图路由 + BU 消歧
│            └────────┬────────┘                  │   │
│   ┌────────┬────────┼────────┬──────────────┐   │   │
│ ┌─▼──┐ ┌───▼───┐ ┌──▼───┐ ┌──▼───┐ ┌────────▼─┐ │   │
│ │政策│ │库存   │ │下单  │ │订单  │ │排产联动   │ │   │
│ │子图│ │子图   │ │子图  │ │进展  │ │分析子图   │ │   │
│ │RAG │ │多轮   │ │写    │ │子图  │ │多跳      │ │   │
│ └─┬──┘ └───┬───┘ └──┬───┘ └──┬───┘ └────┬─────┘ │   │
└───┼────────┼────────┼────────┼──────────┼───────┘   │
    │        │        └→ interrupt() 二次确认 → 审计 → 提交
    │        │        │        │          │           │
┌───▼────────▼────────▼────────▼──────────▼───┐  ┌────▼─────┐
│          业务 API (FastAPI)                  │  │  Dify    │
│     订单 / 库存 / 排产    OpenAPI 3.1        │  │ 知识库    │
└──────────────────┬───────────────────────────┘  │ + 流程    │
                   │         API 失败降级          └────┬─────┘
                   │      ┌────────────────────┐       │
                   │      │  模拟遗留 ERP Web   │       │
                   │      │   + Playwright RPA │       │
                   │      └─────────┬──────────┘       │
       ┌───────────▼──────────────────────────────────┐
       │            PostgreSQL  (GPU 服务器)           │◄─┘
       │  agentsystem 库：业务数据 + 审计 + 检查点      │
       │  dify 库：pgvector 向量 + Dify 元数据          │
       └──────────────────────────────────────────────┘

        ┌──────────────────────────────────────────────┐
        │  模型抽象层 LLMGateway                        │
        │  M0 本机Ollama │ M1 4090 │ M2 A100           │
        │  M3 AutoDL SSH │ M4 阿里云百炼                │
        └──────────────────────────────────────────────┘
```

### 5.1 关键选型与取舍

| 选型 | 决定 | 理由 | 备选 |
|---|---|---|---|
| 向量后端 | **pgvector（由 Dify 托管）** | 检索在 Dify 内，向量库归 Dify 管而非应用层。PostgreSQL 本已必须部署（业务数据 + 审计 + checkpointer），复用即少一个组件、少一套备份与监控体系；且向量与业务表同库，一致性对账可直接用 SQL join | Qdrant / Milvus，改 Dify `VECTOR_STORE` 并重新索引即可切换（详见 §8.3） |
| 检索出口 | **`VectorStorePort` 接口** | 真正需要抽象的不是向量库（Dify 已替我们抽象），而是「是否继续用 Dify 检索」这个决策点。本期只实现读侧，写入权归 Dify 独占（§8.4） | — |
| 请求入口 | **FastAPI Gateway** | 统一鉴权、限流、审计埋点与分流。LangGraph 是网关后的执行器之一，不是入口本身 | — |
| 前端 | **React 19 + Vite + Ant Design 6** | 团队现有 React 栈；AntD 的 Modal / Table / Card 直接支撑二次确认弹窗、审计追溯与引用卡片 | Streamlit（快 2 天，观感偏糙） |
| Agent 编排 | **LangGraph** | 代码即流程，天然可 git diff、可单测、可进 CI，契合「git 管理全生命周期」约束 | — |
| 流程编排 | **Dify** | 业务人员可自助迭代的可视化层 | n8n |
| Agent 拓扑 | **Supervisor + 五子图** | 五场景边界天然清晰；子图各持槽位状态，避免全局 state 污染 | 单一大图（可推翻项） |

### 5.2 部署拓扑

- **本机（macOS, 16GB）**：仅开发环境——React dev server、FastAPI + LangGraph 热重载、Ollama(M0)、Docker 本地 PostgreSQL
- **GPU 服务器（Linux）**：vLLM(M1/M2)、Dify 全套容器、Embedding/Rerank 服务、**PostgreSQL + pgvector（唯一权威实例）**
- **AutoDL（按需）**：vLLM(M3)，经 SSH 端口转发接入
- **阿里云百炼**：M4，OpenAI 兼容 HTTP

> **约束说明**：本机 16GB 统一内存无法承载 Dify 全家桶（约 6 容器）+ PostgreSQL + 应用服务，因此 **Dify 与 PostgreSQL 均部署在 GPU 服务器侧**。

#### PostgreSQL 实例归属

选用 pgvector 后，Dify 需访问 PostgreSQL。跨机访问不合理，而两侧各起一个实例则失去「少一个组件」这一核心优势。因此：

| | 部署 | 内容 |
|---|---|---|
| **权威实例** | GPU 服务器（Linux） | `agentsystem` 库（业务数据 + 审计 + checkpointer）与 `dify` 库（向量 + Dify 元数据），同实例、不同 database，逻辑隔离 |
| 开发实例 | 本机 Docker | 结构相同，靠配置切换；仅供本地开发与单测 |

生产形态本就应在 Linux 服务器上，本机 macOS 只是开发机。这样本机负载进一步下降。

---

## 6. 模型部署矩阵与抽象层

### 6.1 五档部署矩阵

| 档位 | 部署形态 | 模型 | 角色 |
|---|---|---|---|
| **M0** | 本机 Ollama | Qwen3-8B | 单测 / CI 跑通链路，**不进对比结论** |
| **M1** | 4090 24G + vLLM | **Qwen3-30B-A3B-AWQ** | 成本 / 延迟最优候选 |
| **M2** | A100 80G + vLLM | **Qwen3-32B BF16** | 准确率上限基准 |
| **M3** | AutoDL + vLLM + SSH 隧道 | 同 M1/M2 权重 | 弹性成本对比 |
| **M4** | 阿里云百炼 API | qwen3-32b | 零运维基准 |

**核心对照组锁定 M2 vs M4**：同系列权重、同参数规格，变量仅剩「部署方式」，是唯一公平的对比。M1/M3 作为成本维度补充数据点。

> **4090 上的选型说明**：Qwen3-32B-AWQ 权重约 19GB，24G 卡上 KV cache 仅剩约 3GB，上下文与并发均被卡死。Qwen3-30B-A3B-AWQ 为 MoE 架构，每 token 仅激活 3B，同为 4bit 约 17GB，吞吐高出数倍，对延迟指标显著友好。
>
> **型号核实提示**：模型命名基于 2026-05 前的公开信息。实施前应核实 Qwen 系列当期最新版本号，如有更新则同步调整。

### 6.2 抽象层设计

远端 vLLM 与云端 API 均为 OpenAI 兼容接口，因此差异可收敛为三个配置项：

```yaml
# config/models.yaml
tiers:
  M0: { base_url: "http://localhost:11434/v1", model: "qwen3:8b",  api_key_env: null }
  M1: { base_url: "http://gpu-4090:8000/v1",   model: "Qwen3-30B-A3B-AWQ", api_key_env: VLLM_API_KEY }
  M2: { base_url: "http://gpu-a100:8000/v1",   model: "Qwen3-32B",         api_key_env: VLLM_API_KEY }
  M3: { base_url: "http://127.0.0.1:18000/v1", model: "Qwen3-32B",         api_key_env: VLLM_API_KEY }  # SSH -L 隧道
  M4: { base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen3-32b", api_key_env: DASHSCOPE_API_KEY }
```

`LLMGateway` 统一职责：档位路由、超时与重试、token 计量、延迟埋点、失败降级、**将 model_tier 写入每条审计记录**（评测归因的前提）。

**AutoDL 连接管理**：AutoDL 实例通常不提供公网 IP，需通过 SSH 端口转发（`ssh -L 18000:localhost:8000`）将远端 vLLM 端口映射到本地。部署文档需包含隧道保活与断线重连方案。

**API Key 管理**：全部经环境变量注入，`.env` 已在 `.gitignore` 中排除，仓库仅保留 `.env.example`。

---

## 7. Dify / LangGraph 职责分解

### 7.1 判定标准

任一命中「是」即归 LangGraph：

| # | 测试 | 命中则 |
|---|---|---|
| T1 | **事务性**：失败需要回滚 / 幂等 / 补偿吗？ | LangGraph |
| T2 | **审计强制**：需要进不可篡改审计日志吗？ | LangGraph |
| T3 | **状态复杂度**：需跨多轮维护结构化状态、中断后精确恢复吗？ | LangGraph |
| T4 | **自助迭代**：业务人员会想每周自己改它吗？ | **Dify** |

### 7.2 硬红线

> **所有写业务库的操作必须经 LangGraph 执行。Dify 只能「发起请求」，不能直接落库。**

理由：审计要求「所有写操作必须有二次确认与操作日志」。Dify 的运行日志不是审计日志，不满足留痕不可篡改的要求。写端点不对 Dify 开放。

### 7.3 LangGraph 承担（7 项，事务主干）

| # | 场景 | 命中测试 |
|---|---|---|
| L1 | 意图路由 + 槽位填充 + 澄清追问状态机（追问轮次控制、上下文继承） | T3 |
| L2 | **创建订单全链路**：槽位抽取 → 缺参追问 → 库存校验 → `interrupt()` 二次确认 → 提交 → 审计 → 幂等 | T1 T2 T3 |
| L3 | **API 失败降级 RPA**：超时判定 → 重试 → 切 Playwright → 结果回填 → 一致性校验 | T1 T2 |
| L4 | **跨源联动分析**：订单 × 排产 × 库存三方 join 后推理交期延误 | T3 |
| L5 | 会话检查点持久化（刷新页面可续跑） | T3 |
| L6 | 审计埋点（工具入参/出参/耗时/模型档位/用户身份） | T2 |
| L7 | 模型档位 M0–M4 切换与 A/B 评测钩子 | T2 |

### 7.4 Dify 承担（12 项，全部纳入本期）

| # | 场景 | Dify 能力 | 业务价值 |
|---|---|---|---|
| D1 | **营销政策知识库**（8 份文档上传/切块/混合检索/重排/引用归属） | 知识库 + Retrieval 节点 | 业务人员自助传文档调参数，无需发版 |
| D2 | **库存不足 → 转人工审批流** | Workflow 条件分支 + HTTP + 等待回调 | brief 点名的复合流程 |
| D3 | **排产延误预警通知**（生成话术 → 推钉钉/企微/邮件） | Workflow + 工具节点 | 主动服务，非事务 |
| D4 | **标注回复（Annotation Reply）**：业务专家标注正确答案，下次直接命中 | 原生标注 | 人在回路演示 |
| D5 | **SOP / FAQ / 制度类纯知识问答**（含色差判定标准查询） | Chatflow | 无事务，纯检索 |
| D6 | **报表式汇总**：「本周华东区订单概览」取数 → LLM 成文 | HTTP + LLM 节点 | 只读，可自助改话术 |
| D7 | **意图分类器 A/B 对照组**（Dify Question Classifier vs LangGraph 路由） | Question Classifier | 直接产出 PoC 对比数据 |
| D8 | **Prompt 可视化迭代与日志回放** | Prompt IDE + 日志 | 产品/业务改 prompt 不发版 |
| D9 | **角色话术风格切换**（销售 / 生产 / 管理层三套口径） | 会话变量 + 模板 | 面向业务人员差异化 |
| D10 | **开场白 + 下一步问题建议** | 原生能力，零代码 | 降低使用门槛 |
| D11 | **内容审查与敏感词**（价格政策外泄防护） | 原生 Moderation | 合规演示 |
| D12 | **RPA 执行前的人工确认单**（是否执行由 Dify 出表单，如何执行仍在 L3） | Workflow 表单 | 审批与执行解耦 |

### 7.5 接口契约

| 方向 | 机制 | 要点 |
|---|---|---|
| LangGraph → Dify | HTTP 调 Dify Service API（`/v1/workflows/run`、`/v1/chat-messages`） | 携带 `user`、`conversation_id`、**幂等 key** |
| Dify → LangGraph | 业务 API 的 OpenAPI 3.1 定义**直接导入 Dify 成为自定义工具** | 仅开放只读 `/tools/*` 端点，写端点不暴露 |
| 审计闭环 | Dify 每条 workflow 末尾加 HTTP 节点回写审计表 | 保证 Dify 侧调用同样留痕，`source='dify'` |

### 7.6 请求分流

统一入口是 **FastAPI Gateway**，不是 LangGraph。D5（SOP/FAQ 纯知识问答）与 D6（报表汇总）是无状态只读请求，让它们背上 LangGraph 的状态机与 checkpoint 开销纯属浪费。

| 请求类型 | 路径 | 理由 |
|---|---|---|
| 有状态、多轮澄清、事务、**全部写操作** | Gateway → LangGraph Supervisor | 需要状态机、检查点、`interrupt()`、审计 |
| 无状态纯知识问答（D5）、只读汇总（D6） | Gateway → Dify 直通 | 少一层状态机，延迟显著降低 |

鉴权、限流与审计埋点在 Gateway 统一完成，两条路径都覆盖。硬红线不变：写端点仅 LangGraph 可达，Dify 直通路径为只读。

---

## 8. RAG 方案

### 8.1 主检索路径：Dify 知识库为主

8 份文档全部进 Dify 知识库，业务人员可自助上传、调整切块、调整检索参数、做标注；LangGraph 通过 Dify Retrieval API 取召回。评测脚本直接打 Dify 检索 API 计算召回率与引用准确率。

**管线**：文档解析 → 切块 → 向量化（bge-m3）→ 混合检索（向量 + 关键词）→ 重排（bge-reranker-v2-m3）→ 带引用作答。

**元数据 schema**（支撑「年度 × 区域 × 产品」三维过滤，是 S1 场景的准确率关键）：

```
bu_code | doc_type | year | region | product_line | effective_from | effective_to | source_file | page
```

### 8.2 必须补的洞：检索配置版本化

选择「Dify 知识库为主」的代价是**切块策略与检索参数不进 git diff**，与「git 管理全生命周期」约束冲突。

**堵法**：P3 交付 `scripts/dify_snapshot.py`，定期导出知识库配置（切块大小 / 重叠 / 检索模式 / 混合权重 / rerank 模型 / Top-K）为 YAML 提交入库。使配置变更可 diff、可回滚，并可在评测报告中标注「本次跑分对应哪版检索配置」。

### 8.3 向量后端与可切换性

向量库的所有权在 Dify，不在应用层。切换方式与成本：

| 层 | 所有者 | 切换方式 | 成本 |
|---|---|---|---|
| Dify 知识库的向量后端 | Dify | 改 Dify `.env` 的 `VECTOR_STORE` 后**重新索引** | 8 份文档重嵌入，分钟级 |
| 应用侧检索出口 | 我们 | `VectorStorePort` 接口，实现类可替换 | 零，接口契约不变 |

**默认 pgvector**，理由有四：

1. **少一个组件**——PostgreSQL 本已必须部署（业务数据、审计日志、LangGraph checkpointer），复用即少一章部署文档、少一套备份策略、少一个监控对象、少一个故障点
2. **运维体系统一**——企业已有 PostgreSQL 的备份/恢复/权限/监控能力，无需为新组件建立新的运维知识
3. **SQL 可达**——一致性对账（§8.4）可直接 SQL join 业务表，如「政策文档中提及岩板的 chunk 与库存表中的岩板产品是否对得上」；换成独立向量库则需另写代码
4. **规模完全够用**——8 份文档、几千 chunk，HNSW 索引毫无压力

> **已知边界与重估触发条件**：pgvector 的 HNSW 索引在**高选择性过滤**下存在召回衰减——过滤条件筛掉绝大部分数据时，图遍历可能凑不够候选。在本项目几千 chunk 的规模下不发生（过滤后可退化为精确扫描，仍是毫秒级）。**当语料超过 10 万 chunk 且检索普遍带高选择性过滤时，应重新评估 Qdrant / Milvus**；届时 `VectorStorePort` 已就位，切换成本仍只是重新索引。

应用侧只依赖 `VectorStorePort`（完整契约见 §8.4）。本期实现 `DifyRetriever`；若后续放弃 Dify 检索，Agent 子图无需改动。

**验证任务**（P5）：切换到 Qdrant 后用同一测试集重跑评测，证明可切换性并记录召回率差异，结果直接作为报告数据点。

> **P1 待核实**：Dify 当期版本实际支持的向量后端清单、pgvector 的表结构与索引类型（HNSW / IVFFlat）、以及各后端在元数据过滤能力上的差异。不凭记忆写入文档。

### 8.4 向量数据纳管：写入权与治理

「统一纳管」在本期的正确形态是**统一读取口径与统一治理规范，而非统一写入通道**。写入通道有且只能有一个。

#### 写入权归属

| 方案 | 谁写 chunk | 是否双写 | 本期建服务 | D1 业务自助 |
|---|---|---|---|---|
| **甲（本期采纳）** | **Dify 独占** | 否 | 不建，只定接口 + 出设计 | 保留 |
| 乙 | Dify 独占 | 否 | 建只读 + collection 级运维服务 | 保留 |
| 丙 | 我们独占 | 否 | 建完整服务，Dify 改用外部知识库 API | **作废** |
| ~~双写~~ | Dify 与我们都写 | **是** | — | 引用溯源断裂 |

#### 为何必须排除双写

Dify 在向量库之外维护自己的元数据表（document / segment 记录）与之同步。外部程序直接写向量库，Dify 元数据不知情，后果有三：

1. Dify 界面中的文档列表与向量库实际内容不一致
2. Dify 触发重建索引时会覆盖或删除外部写入的数据
3. **引用溯源断裂**——Dify 找不到对应 segment 记录

第 3 条是硬伤：验收指标含「RAG 引用准确率 ≥85%」，而引用溯源正依赖 Dify 的 segment 元数据。双写等于拆掉该指标的地基。

#### VectorStorePort 接口（本期只实现读侧）

```python
class VectorStorePort(Protocol):
    """向量数据统一接口。
    写入权本期归 Dify 独占，故写侧契约先定义、不实现。"""

    # ---- 读侧：本期实现 DifyRetriever ----
    async def retrieve(self, query: str, filters: dict,
                       top_k: int) -> list[RetrievedChunk]: ...
    async def count(self, collection: str,
                    filters: dict | None = None) -> int: ...

    # ---- collection 级运维：本期定义，方案乙时实现 ----
    async def create_collection(self, name: str, dim: int, metric: str) -> None: ...
    async def drop_collection(self, name: str) -> None: ...
    async def switch_alias(self, alias: str, target: str) -> None: ...

    # ---- chunk 级写入：本期定义不实现，写入权归 Dify ----
    async def upsert(self, collection: str, chunks: list[Chunk]) -> None: ...
    async def delete_by_doc(self, collection: str, doc_id: str) -> None: ...
```

#### 环境隔离（零成本方案）

Dify **知识库命名规范** `{env}_{bu}_{doctype}_v{n}`，单实例多命名空间隔离，无需独立实例：

```
prod_bua_policy_v3      应用配置 policy_dataset_id ──> 指向 v3
dev_bub_manual_v1       应用配置 manual_dataset_id ──> 指向 v1
```

#### 蓝绿重建索引：切在 Dify 层，不切在向量库表层

换 embedding 模型需全量重嵌入。**切换动作必须发生在 Dify 知识库层，而非向量库表层**——写入权归 Dify，我们不应直接操作它的表（否则即落入双写反模式）。正确流程：

```
建新知识库 prod_bua_policy_v4  →  重新索引（旧库继续服务）
    →  评测新库召回质量  →  切应用配置 dataset_id 指向 v4  →  下线 v3
```

此方案是 **backend-agnostic** 的：pgvector 与 Qdrant 下流程完全一致，因为它不依赖任何特定向量库的别名能力。

#### 独立交付物：《向量数据纳管服务设计》

作为 PoC 的独立交付文档（非架构文档子节），供后续平台化立项使用。内容清单：

1. 定位与边界：纳管什么、不纳管什么
2. 写入权模型与双写反模式
3. Collection 生命周期与别名切换（蓝绿重建索引）
4. CRUD API 契约（OpenAPI 定义）
5. 环境与租户隔离模型
6. **Embedding 模型版本管理与全量重嵌入流程**（纳管服务最实的价值点）
7. 跨后端迁移方案（pgvector ↔ Qdrant ↔ Milvus）
8. 一致性对账：源文档与向量库
9. 备份与恢复
10. 监控指标与告警阈值
11. 演进路线：接口先行 → 只读运维服务 → 平台化，三阶段

---

## 9. Agent 内核设计

### 9.1 拓扑：Supervisor + 五子图

Supervisor 仅负责意图识别、**BU 消歧**与子图调度。每个子图自持槽位状态——染缸号、色号、等级、门幅这类槽位仅在库存子图内有意义，不应污染全局 state。

> **BU 消歧示例**：用户问「查一下白色的库存」，Supervisor 需追问是**布**还是**砖**，因为两个事业部的色号体系与库存维度完全不同。

### 9.2 三级意图路由

Supervisor 若每轮都调主模型做意图识别，将额外增加 0.5–2s 延迟，直接侵蚀延迟指标。改为三级路由，逐级下沉：

| 级别 | 手段 | 延迟 | 预期覆盖 |
|---|---|---|---|
| L1 | **规则前置**：订单号正则 `SO-\d{4}-\d{6}`、「库存 / 排产 / 政策」等强关键词命中 | ~0ms | 40–60% |
| L2 | **小模型分类**：M0 档 Qwen3-8B 或 embedding + 分类头 | ~100–200ms | 大部分剩余 |
| L3 | **主模型兜底**：仅真正模糊的请求走 M1–M4 + function calling | 0.5–2s | 少数 |

叠加**会话内意图粘性**：多轮追问中若上轮意图已确定且用户未明显切换话题，直接沿用不重新识别。这能消除澄清轮次中绝大部分路由开销。

D7（Dify Question Classifier A/B 对照组）正是用来验证这套路由：同一测试集对比「Dify 分类器 vs 三级路由」的准确率与延迟，直接产出报告数据。

### 9.3 多轮澄清策略

- 槽位定义按子图声明，缺失时按**业务重要性排序**逐个追问，而非一次抛出全部缺项
- 追问轮次上限 3 轮，超限则给出「已知条件下的最佳结果 + 明确说明未确定项」，避免无限追问
- 用户中途切换意图时，Supervisor 保留原子图状态快照，支持回切

### 9.4 状态持久化

LangGraph checkpointer 落 PostgreSQL，以 `session_id` 为键。前端刷新或断线后可精确恢复到中断点，含未完成的二次确认。

---

## 10. 前端实现规划

### 10.1 技术栈与部署形态

| 项 | 决定 |
|---|---|
| 框架 | **React 18 + TypeScript**（团队现有技术栈） |
| 构建 | **Vite** |
| UI 组件库 | **Ant Design 6** |
| 状态管理 | Zustand（轻量够用，不引入 Redux） |
| 流式传输 | SSE（fetch + ReadableStream） |

**部署形态：浏览器访问的 SPA，生产环境不需要 Node.js 运行时。**

此点需明确，因常被误解：Vite 的构建产物是纯静态文件（HTML / JS / CSS），由 Nginx 直接托管。**Node.js 仅在开发期与构建期需要，运行期没有 Node 进程。** 需要常驻 Node 服务的是 Next.js 一类 SSR 方案——本项目不需要 SSR：内部系统、登录后使用、无 SEO 诉求、首屏性能不敏感。

```
浏览器 ──> Nginx ──┬─> /      静态资源（Vite 构建产物）
                   └─> /api   反向代理 ──> FastAPI Gateway
                                            └─> SSE 流式
```

### 10.2 参照的开源实践

> **版本选型依据（2026-08-28 核实 npm registry）**：`antd@latest` = **6.6.2**，v6.x 已发布 33 个正式版，早期采用风险窗口已过。v5.x 最新为 5.29.3，已非 `latest`。
> 选 6 而非 5 的决定性理由：**本项目是绿地开发，不存在留在 v5 的最大理由——迁移成本**。此外 antd 6 的 `peerDependencies` 为 `react >=18`，原生支持 React 19；而 antd 5 在 React 19 下需额外引入 `@ant-design/v5-patch-for-react-19` 补丁包。
>
> **React 版本**：`react@latest` = **19.2.8**（2026-07-21）。绿地项目直接采用 React 19，与 antd 6 的 `peerDependencies` 相符。
>
> **ProComponents 排除（2026-08-28 核实）**：`@ant-design/pro-components@latest` = **2.8.10**，发布于 **2025-07-17**，距今 13 个月无新版；其 `peerDependencies` 仅声明 `antd: ^4.24.15 || ^5.11.2`，**不支持 antd 6**。故本项目不引入 ProComponents——即使留在 antd 5，依赖一个停更逾年的包本身亦属风险。所需组件（Modal / Card / Table / Select / Tag / Timeline）均在 antd 主包内，审计追溯视图以原生 `Table` 实现即可，无功能损失。

| 参照 | 借鉴内容 |
|---|---|
| **Dify Web 前端** | 流式对话 + 引用来源展示 + 标注回复，与本项目需求重合度最高 |
| **Lobe Chat** | 模型档位切换的 UI 范式，对应 M0–M4 切换器 |
| **Open WebUI** | 会话管理与模型选择的交互设计 |
| **assistant-ui / Vercel AI SDK UI** | SSE 流式渲染、工具调用中间态展示 |

### 10.3 组件清单

| 组件 | 职责 | AntD 基础件 |
|---|---|---|
| 对话流 | 消息渲染、流式打字、工具调用中间态 | List + Skeleton |
| 引用来源卡片 | 展示 RAG 命中的文档名 / 页码 / 片段，可展开原文 | Card + Collapse |
| 二次确认弹窗 | 展示订单全字段供核对，携带 `confirm_token` | Modal + Descriptions |
| 档位切换器 | M0–M4 切换，显示当前档位与延迟 | Select + Tag |
| 澄清追问区 | 缺失槽位的快捷选项（色号、等级、是否跨缸） | Radio.Group + Form |
| 审计追溯视图 | 按 `trace_id` 查看工具调用链、耗时、档位 | Table + Timeline |

---

## 11. 并发与性能设计

### 11.1 PoC 并发目标

**≤10 并发会话**。PoC 不是压测项目，该目标对应演示与小范围试用的真实量级。P5 做一次 10 并发压测出数，M1（4090）与 M2（A100）两档分别测，结果直接支撑选型建议。

### 11.2 瓶颈分析

LangGraph 本身不是瓶颈——它是跑在 FastAPI async event loop 里的 Python 库，编排开销微秒级。真正的瓶颈有三处：

| 瓶颈 | 量级 | 应对 |
|---|---|---|
| **vLLM 推理并发** | 4090 24G 跑 Qwen3-30B-A3B-AWQ 约 4–8 路；A100 80G 跑 Qwen3-32B BF16 约 20–40 路。随上下文长度显著变化 | 硬上限，本期不试图突破；压测据此标定 |
| **checkpointer 写库** | 每次节点转换写一次 PostgreSQL，高并发下成为真瓶颈 | `AsyncPostgresSaver` + 连接池；只读子图关闭 checkpoint |
| **同步阻塞调用** | 工具函数中若存在同步 HTTP / DB 调用会阻塞 event loop，并发直接塌方 | **全链路 async 强制约束**，配 lint 规则拦截 |

### 11.3 生产化路径（本期不实现）

- FastAPI 多 worker（gunicorn + uvicorn worker）；checkpointer 走 PostgreSQL 天然支持多进程共享状态
- 长任务转异步队列（ARQ / Celery）+ SSE 推送结果
- LLM 侧横向扩容，或高峰期溢出到云端 API

---

## 12. 写操作二次确认与审计

### 12.1 流程

```
槽位齐备 → 库存校验 → 生成确认卡片（含 confirm_token）
    → LangGraph interrupt() 挂起
    → 前端展示：客户/产品/色号/等级/数量/交期/同批要求/预计交期/适用政策
    → 用户确认 → 携 confirm_token 恢复执行
    → 调写入 API → 审计落库 → 返回订单号
```

### 12.2 幂等与审计要求

- `confirm_token` 一次性，同一 token 只能成功执行一次，重复提交返回原结果而非重复创建
- 审计记录须包含：`trace_id`、`user_id`、`model_tier`、入参出参（脱敏）、耗时、`confirm_token`、`confirmed_at`
- 用户取消确认同样落审计，`status='cancelled'`
- 审计表仅追加不更新，不提供删除接口

---

## 13. RPA 降级路径

### 13.1 触发与流程

```
业务 API 超时(>N秒) / 5xx / 熔断器打开
    → LangGraph 判定降级 (L3)
    → Dify 出人工确认单 (D12)
    → 确认后 Playwright 操作模拟遗留 ERP Web 界面
    → 截图取证 → 结果回填 → 一致性校验 → 审计(source='rpa', status='degraded')
```

### 13.2 交付物

- **模拟遗留 ERP Web 界面**：一个无 API 的传统表单式 Web 应用，作为 RPA 的操作对象（否则 RPA 无对象可操作）
- **Playwright 实现**：登录、导航、填单、提交、截图取证，跨平台可 CI
- **影刀流程设计文档**：流程图、元素定位策略、异常处理与重试、与本系统的调用契约、审计日志要求，供后续 Windows 环境实施

---

## 14. 验收指标与评测方案

| 指标 | 目标 | 说明 |
|---|---|---|
| 五个主线场景端到端成功率 | **≥90%** | 测试集 ≥40 条（5 场景 × 6 条 + 边界 10 条） |
| RAG 答案引用准确率 | **≥85%** | 引用来源与答案内容一致且可溯源到原文 |
| 单轮响应平均延迟 | M2 P50 ≤5s / M4 P50 ≤3s / M1 P50 ≤4s | 流式首 token 与完整响应分别统计 |
| 边界情况处理 | 全部正确 | 缺参追问、模糊意图、BU 消歧、API 失败降级 |
| **并发承载** | **≤10 并发会话不劣化** | P5 做 10 并发压测，M1 / M2 两档分别测；未达标则作为报告结论之一 |
| 双部署对比结论 | 出数 | 准确率、延迟、成本、数据安全性四维 |

**测试集构成**：S1–S5 各 6 条常规用例；边界 10 条覆盖缺参、模糊意图、跨 BU 歧义、超范围提问、API 超时、库存不足、幂等重复提交、追问轮次超限、拒绝确认、越权查询。

**评测维度归因**：每条用例在 M1/M2/M3/M4 四档各跑一次，结果按 `model_tier` 归因，输出对比矩阵。M0 仅用于 CI 冒烟，不进对比结论。

---

## 15. 分阶段实施计划

### 里程碑一：核心链路可演示（P1–P3，18 人天）

| 阶段 | 交付 | 验收信号 | 人天 |
|---|---|---|---|
| **P1 地基** | git 工作流、`LLMGateway`（五档配置化切换）、审计日志基座、`.env` 体系、CI | `make chat TIER=M2` 与 `TIER=M4` 均能回话 | 3 |
| **P2 业务数据 + API** | 双 BU 三表建模、≥100 条/表脱敏模拟数据、FastAPI + OpenAPI 3.1 | Swagger 可点通，五类查询返回真实感数据 | 5 |
| **P3 RAG + Agent 主链路** | 8 份文档、Dify 知识库、检索配置快照脚本、Supervisor + 五子图、多轮澄清、写操作二次确认 + 审计、React 对话界面 | 五个主线场景端到端跑通，可演示 | 10 |

### 里程碑二：完整能力与交付物（P4–P5，13.5 人天）

| 阶段 | 交付 | 验收信号 | 人天 |
|---|---|---|---|
| **P4 Workflow + RPA** | Dify 十二项场景、模拟遗留 ERP Web UI、Playwright RPA、API 超时自动降级、影刀流程设计文档 | 「API 挂掉 → RPA 兜底 → 回填结果」可演示 | 7 |
| **P5 评测 + 交付物** | ≥40 条测试集、M1/M2/M3/M4 四档评测、**10 并发压测**、**向量库切换验证（pgvector → Qdrant 重跑评测）**、架构文档、WBS、API 文档、落地手册、部署运维、资源计划报告、**《向量数据纳管服务设计》** | 报告出数，给出选型建议 | 6.5 |

### 工期换算

**总工作量 31.5 人天。** 受串行依赖限制：

| 团队规模 | 日历周期 | 说明 |
|---|---|---|
| 1 人 | 约 6 周 | 全串行 |
| 2 人 | 约 4 周 | 后端/编排 与 RAG/前端 并行 |
| 3 人 | 约 3 周 | 再拆出评测/文档，边际收益开始递减 |

### 分支策略

- `main` 受保护，仅接受经评审的合并
- 各阶段走 `feat/p{N}-*` 功能分支，完成后合并
- 文档修订走 `docs/*` 分支

---

## 16. 风险登记册

| # | 风险 | 影响 | 应对 |
|---|---|---|---|
| R1 | Qwen 型号命名基于 2026-05 前信息，可能已更新 | 选型偏差 | P1 开始前核实当期最新版本，同步调整配置 |
| R2 | 本机 16GB 内存承载不了完整本地栈 | 开发受阻 | Dify 部署至 GPU 服务器；本机仅跑轻量栈 |
| R3 | Docker daemon 当前未运行 | P4 受阻 | P4 开始前启动 Docker Desktop |
| R4 | 阿里云百炼 API Key 尚未提供 | M4 无法出数 | 抽象层与评测脚本先行；Key 到位后一条命令重跑补齐 |
| R5 | AutoDL 无公网 IP，SSH 隧道易断 | M3 数据不稳 | 隧道保活 + 自动重连；M3 为补充数据点，非核心对照组 |
| R6 | Dify 检索配置不进 git | 违反 git 全生命周期约束 | `dify_snapshot.py` 配置快照版本化（§8.2） |
| R7 | 十二项 Dify 场景范围较大 | 工期超支 | 里程碑一为完整交付切点，中期可据反馈砍 P4/P5 范围 |
| R8 | 双 BU 使数据与文档工作量翻倍 | 工期超支 | 共用同构数据模型，仅属性字段差异化 |
| R9 | 工具函数中混入同步阻塞调用 | 并发直接塌方，延迟指标失真 | 全链路 async 强制约束 + lint 规则拦截；压测作为兜底检出手段 |
| R10 | Dify 当期版本对 pgvector 的表结构与索引类型未核实 | 向量后端选型返工 | P1 核实支持清单与索引实现；`VectorStorePort` 使应用侧不受影响 |
| R12 | 750 行设计文档中的硬约束散落各章，实现者（人或 agent）漏看单条约束 | 审计缺失、双写、同步阻塞等红线被击穿 | 抽取为 `docs/CONSTITUTION.md` 九条原则（§2.4）；每模块动工前逐条核对，每阶段末做收敛检查 |
| R11 | 后续有人绕过 Dify 直接写向量库 | 引用溯源断裂，击穿 ≥85% 引用准确率指标 | §8.4 写入权模型写入设计文档并在代码中以接口分层强制；`VectorStorePort` 写侧本期不提供实现。**pgvector 下该风险更高**——向量表与业务表同库，SQL 可直达，须以数据库账号权限隔离：应用账号对 `dify` 库只读 |

---

## 17. 待决事项

| # | 事项 | 需要谁 | 阻塞阶段 |
|---|---|---|---|
| Q1 | 阿里云百炼 API Key | 用户提供 | P5 评测 M4 档 |
| Q2 | GPU 服务器与 AutoDL 的访问方式（地址、SSH 凭据） | 用户提供 | P1 收尾 |
| Q3 | 团队规模确认（决定日历周期是 3 / 4 / 6 周） | 用户决策 | 资源计划报告 |
| Q4 | 通知渠道选型（钉钉 / 企微 / 邮件）用于 D3 | 用户决策 | P4 |
