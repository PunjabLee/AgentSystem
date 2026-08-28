# 企业级智能问答与任务执行系统 PoC — 总体架构设计

## 0. 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.0（设计基线） |
| 创建日期 | 2026-08-28 |
| 状态 | 待评审 |
| 业务域 | 织染（印染事业部）+ 瓷砖洁具（建陶卫浴事业部）双事业部制造集团 |
| 总工作量 | 30 人天 |

### 编号约定

为避免歧义，本文档使用三套互不重叠的编号前缀：

| 前缀 | 含义 | 范围 |
|---|---|---|
| `S1`–`S5` | 主线业务场景 | §3.3 |
| `M0`–`M4` | 模型部署档位 | §6.1 |
| `L1`–`L7` | LangGraph 承担的场景 | §7.3 |
| `D1`–`D12` | Dify 承担的场景 | §7.4 |
| `R1`–`R8` | 风险项 | §14 |
| `Q1`–`Q4` | 待决事项 | §15 |

### 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-08-28 | 初始基线。业务域由家电制造切换为织染 + 建陶卫浴；确认 Dify/LangGraph 十九项场景分解；GraphRAG 移出本期范围 |

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
- 全套交付文档

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
- GraphRAG 作为检索路径的对照实验
- 影刀 RPA 在 Windows 环境的实施
- 真实业务系统对接与生产级权限体系

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
┌──────────────────────────────────────────────────────────────┐
│  对话界面 (React + Vite)                                       │
│  模型档位切换 ▾ | 引用来源卡片 | 二次确认弹窗 | 审计追溯视图        │
└────────────────────────────┬─────────────────────────────────┘
                             │ SSE 流式
┌────────────────────────────▼─────────────────────────────────┐
│              LangGraph Agent 内核  (FastAPI)                  │
│                                                               │
│              ┌─────────────────┐                              │
│              │   Supervisor    │  意图识别 + BU 消歧 + 子图调度  │
│              └────────┬────────┘                              │
│      ┌────────┬───────┼────────┬──────────────┐              │
│   ┌──▼──┐ ┌───▼───┐ ┌─▼────┐ ┌─▼─────┐ ┌─────▼──────┐       │
│   │政策 │ │库存   │ │下单  │ │订单   │ │排产联动     │       │
│   │子图 │ │子图   │ │子图  │ │进展   │ │分析子图     │       │
│   │(RAG)│ │(多轮) │ │(写)  │ │子图   │ │(多跳)      │       │
│   └──┬──┘ └───┬───┘ └──┬───┘ └──┬────┘ └─────┬──────┘       │
└──────┼────────┼────────┼────────┼────────────┼──────────────┘
       │        │        │        │            │
       │        │        └─→ interrupt() 二次确认 → 审计 → 提交
       │        │        │        │            │
  ┌────▼────┐ ┌─▼────────▼────────▼────────────▼─┐  ┌──────────┐
  │  Dify   │ │      业务 API (FastAPI)           │  │模拟遗留   │
  │ 知识库   │ │  订单 / 库存 / 排产  OpenAPI 3.1  │  │ ERP Web  │
  │ + 流程   │ └───────────────┬──────────────────┘  │ (RPA 对象)│
  └─────────┘                 │                      └─────┬────┘
                              │                            │
                    ┌─────────▼──────────┐          ┌──────▼──────┐
                    │    PostgreSQL      │          │ Playwright  │
                    │ 业务数据 + 审计日志  │          │  RPA 兜底    │
                    └────────────────────┘          └─────────────┘

        ┌─────────────────────────────────────────────┐
        │  模型抽象层 LLMGateway                        │
        │  M0 本机Ollama | M1 4090 | M2 A100          │
        │  M3 AutoDL SSH | M4 阿里云百炼               │
        └─────────────────────────────────────────────┘
```

### 5.1 关键选型与取舍

| 选型 | 决定 | 理由 | 备选 |
|---|---|---|---|
| 向量存储 | **pgvector** | 业务数据本已用 PostgreSQL，少一个组件即少一章部署文档；PoC 数据量（几千 chunk）远未触及性能边界 | Qdrant（千万级向量更快，本期用不到） |
| 前端 | **React + Vite** | 二次确认弹窗与引用来源卡片是演示核心卖点，需要可控的交互设计 | Streamlit（快 2 天，观感偏糙） |
| Agent 编排 | **LangGraph** | 代码即流程，天然可 git diff、可单测、可进 CI，契合「git 管理全生命周期」约束 | — |
| 流程编排 | **Dify** | 业务人员可自助迭代的可视化层 | n8n |
| Agent 拓扑 | **Supervisor + 五子图** | 五场景边界天然清晰；子图各持槽位状态，避免全局 state 污染 | 单一大图（可推翻项） |

### 5.2 部署拓扑

- **本机（macOS, 16GB）**：React 前端、FastAPI 业务 API、LangGraph 内核、PostgreSQL + pgvector、Ollama(M0)
- **GPU 服务器（Linux）**：vLLM(M1/M2)、Dify 全套容器、Embedding/Rerank 服务
- **AutoDL（按需）**：vLLM(M3)，经 SSH 端口转发接入
- **阿里云百炼**：M4，OpenAI 兼容 HTTP

> **约束说明**：本机 16GB 统一内存无法承载 Dify 全家桶（约 6 容器）+ PostgreSQL + 向量库 + 应用服务，因此 **Dify 必须部署在 GPU 服务器侧**。

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

---

## 9. Agent 内核设计

### 9.1 拓扑：Supervisor + 五子图

Supervisor 仅负责意图识别、**BU 消歧**与子图调度。每个子图自持槽位状态——染缸号、色号、等级、门幅这类槽位仅在库存子图内有意义，不应污染全局 state。

> **BU 消歧示例**：用户问「查一下白色的库存」，Supervisor 需追问是**布**还是**砖**，因为两个事业部的色号体系与库存维度完全不同。

### 9.2 多轮澄清策略

- 槽位定义按子图声明，缺失时按**业务重要性排序**逐个追问，而非一次抛出全部缺项
- 追问轮次上限 3 轮，超限则给出「已知条件下的最佳结果 + 明确说明未确定项」，避免无限追问
- 用户中途切换意图时，Supervisor 保留原子图状态快照，支持回切

### 9.3 状态持久化

LangGraph checkpointer 落 PostgreSQL，以 `session_id` 为键。前端刷新或断线后可精确恢复到中断点，含未完成的二次确认。

---

## 10. 写操作二次确认与审计

### 10.1 流程

```
槽位齐备 → 库存校验 → 生成确认卡片（含 confirm_token）
    → LangGraph interrupt() 挂起
    → 前端展示：客户/产品/色号/等级/数量/交期/同批要求/预计交期/适用政策
    → 用户确认 → 携 confirm_token 恢复执行
    → 调写入 API → 审计落库 → 返回订单号
```

### 10.2 幂等与审计要求

- `confirm_token` 一次性，同一 token 只能成功执行一次，重复提交返回原结果而非重复创建
- 审计记录须包含：`trace_id`、`user_id`、`model_tier`、入参出参（脱敏）、耗时、`confirm_token`、`confirmed_at`
- 用户取消确认同样落审计，`status='cancelled'`
- 审计表仅追加不更新，不提供删除接口

---

## 11. RPA 降级路径

### 11.1 触发与流程

```
业务 API 超时(>N秒) / 5xx / 熔断器打开
    → LangGraph 判定降级 (L3)
    → Dify 出人工确认单 (D12)
    → 确认后 Playwright 操作模拟遗留 ERP Web 界面
    → 截图取证 → 结果回填 → 一致性校验 → 审计(source='rpa', status='degraded')
```

### 11.2 交付物

- **模拟遗留 ERP Web 界面**：一个无 API 的传统表单式 Web 应用，作为 RPA 的操作对象（否则 RPA 无对象可操作）
- **Playwright 实现**：登录、导航、填单、提交、截图取证，跨平台可 CI
- **影刀流程设计文档**：流程图、元素定位策略、异常处理与重试、与本系统的调用契约、审计日志要求，供后续 Windows 环境实施

---

## 12. 验收指标与评测方案

| 指标 | 目标 | 说明 |
|---|---|---|
| 五个主线场景端到端成功率 | **≥90%** | 测试集 ≥40 条（5 场景 × 6 条 + 边界 10 条） |
| RAG 答案引用准确率 | **≥85%** | 引用来源与答案内容一致且可溯源到原文 |
| 单轮响应平均延迟 | M2 P50 ≤5s / M4 P50 ≤3s / M1 P50 ≤4s | 流式首 token 与完整响应分别统计 |
| 边界情况处理 | 全部正确 | 缺参追问、模糊意图、BU 消歧、API 失败降级 |
| 双部署对比结论 | 出数 | 准确率、延迟、成本、数据安全性四维 |

**测试集构成**：S1–S5 各 6 条常规用例；边界 10 条覆盖缺参、模糊意图、跨 BU 歧义、超范围提问、API 超时、库存不足、幂等重复提交、追问轮次超限、拒绝确认、越权查询。

**评测维度归因**：每条用例在 M1/M2/M3/M4 四档各跑一次，结果按 `model_tier` 归因，输出对比矩阵。M0 仅用于 CI 冒烟，不进对比结论。

---

## 13. 分阶段实施计划

### 里程碑一：核心链路可演示（P1–P3，18 人天）

| 阶段 | 交付 | 验收信号 | 人天 |
|---|---|---|---|
| **P1 地基** | git 工作流、`LLMGateway`（五档配置化切换）、审计日志基座、`.env` 体系、CI | `make chat TIER=M2` 与 `TIER=M4` 均能回话 | 3 |
| **P2 业务数据 + API** | 双 BU 三表建模、≥100 条/表脱敏模拟数据、FastAPI + OpenAPI 3.1 | Swagger 可点通，五类查询返回真实感数据 | 5 |
| **P3 RAG + Agent 主链路** | 8 份文档、Dify 知识库、检索配置快照脚本、Supervisor + 五子图、多轮澄清、写操作二次确认 + 审计、React 对话界面 | 五个主线场景端到端跑通，可演示 | 10 |

### 里程碑二：完整能力与交付物（P4–P5，12 人天）

| 阶段 | 交付 | 验收信号 | 人天 |
|---|---|---|---|
| **P4 Workflow + RPA** | Dify 十二项场景、模拟遗留 ERP Web UI、Playwright RPA、API 超时自动降级、影刀流程设计文档 | 「API 挂掉 → RPA 兜底 → 回填结果」可演示 | 7 |
| **P5 评测 + 交付物** | ≥40 条测试集、M1/M2/M3/M4 四档评测、架构文档、WBS、API 文档、落地手册、部署运维、资源计划报告 | 报告出数，给出选型建议 | 5 |

### 工期换算

**总工作量 30 人天。** 受串行依赖限制：

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

## 14. 风险登记册

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

---

## 15. 待决事项

| # | 事项 | 需要谁 | 阻塞阶段 |
|---|---|---|---|
| Q1 | 阿里云百炼 API Key | 用户提供 | P5 评测 M4 档 |
| Q2 | GPU 服务器与 AutoDL 的访问方式（地址、SSH 凭据） | 用户提供 | P1 收尾 |
| Q3 | 团队规模确认（决定日历周期是 3 / 4 / 6 周） | 用户决策 | 资源计划报告 |
| Q4 | 通知渠道选型（钉钉 / 企微 / 邮件）用于 D3 | 用户决策 | P4 |
