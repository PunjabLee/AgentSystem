# 企业级智能问答与任务执行系统 PoC — 总体架构设计

## 0. 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v2.2（独立评审修订） |
| 创建日期 | 2026-08-28 |
| 状态 | 待评审 |
| 业务域 | 印染 + 建陶瓷砖 + 卫浴洁具 **三事业部**制造集团（建陶瓷砖为主营） |
| 总工作量 | 65.70 人天（v2.2） |
| 团队规模 | 1 人（全串行） |
| 日历周期 | 16.4 周，含缓冲约 21 周 |
| 关联文档 | [专家团评审合并报告](../../REVIEW-PANEL-2026-08-28.md) · [项目宪法](../../CONSTITUTION.md) · [技术栈版本矩阵](../../TECH-STACK-VERSIONS.md) |

### 编号约定

为避免歧义，本文档使用以下互不重叠的编号前缀：

| 前缀 | 含义 | 范围 |
|---|---|---|
| `S1`–`S5` | 主线业务场景 | §3.3 |
| `M0`–`M4` | 模型部署档位 | §6.1 |
| `L1`–`L7` | LangGraph 承担的场景 | §7.3 |
| `D1`–`D12` | Dify 承担的场景 | §7.4 |
| `R1`–`R16` | 风险项 | §16 |
| `Q1`–`Q5` | 待决事项 | §17 |

### 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-08-28 | 初始基线。业务域由家电制造切换为织染 + 建陶卫浴；确认 Dify/LangGraph 十九项场景分解；GraphRAG 移出本期范围 |
| v1.1 | 2026-08-28 | 首轮评审修订。修正 §5.1 与 §8.1 关于向量库归属的自相矛盾（向量后端归 Dify 托管，默认改 Qdrant，新增 `RetrieverPort` 接口）；新增 FastAPI Gateway 请求分流层，LangGraph 不再是唯一入口；意图识别改三级路由以控延迟；新增 §10 前端实现规划与 §11 并发与性能设计。工期 30 → 31 人天 |
| v1.2 | 2026-08-28 | 新增 §8.4 向量数据纳管：明确写入权本期归 Dify 独占，排除双写反模式（双写会切断 Dify segment 元数据链路，直接击穿引用准确率指标）；`RetrieverPort` 扩展为 `VectorStorePort`，本期只实现读侧；新增 collection 命名规范做零成本环境隔离；《向量数据纳管服务设计》列为独立交付物。工期 31 → 31.5 人天 |
| v1.3 | 2026-08-28 | 向量后端由 Qdrant 改回 **pgvector**。v1.2 的论证有缺陷——推翻了「本机内存约束」这一条 pgvector 论据后即改选 Qdrant，未重新审视其余论据（少一个组件、运维体系统一、SQL 可达便于对账），而这些在本项目规模下均成立；Qdrant 的 payload 索引优势在几千 chunk 量级不发生。同时记录重估触发条件（>10 万 chunk 且高选择性过滤）。连带修正两处：PostgreSQL 由本机移至 GPU 服务器作为唯一权威实例（否则两侧各起一个实例将抵消「少一个组件」的优势）；蓝绿重建索引改在 Dify 知识库层完成而非向量库表层，避免落入双写反模式且做到 backend-agnostic。R11 补充 pgvector 下的权限隔离要求 |
| v1.4 | 2026-08-28 | 前端 UI 框架由 Ant Design 5 改为 **Ant Design 6**，React 由 18 改为 **19**（`react@latest` = 19.2.8）；核实后排除 ProComponents（`@ant-design/pro-components` 停更逾年且不支持 antd 6）。原选 v5 系依训练数据惯性而非当下判断；核实 npm registry 后确认 `latest` 已是 6.6.2（v6.x 共 33 个正式版），且绿地项目不存在留在 v5 的最大理由——迁移成本；antd 6 另原生支持 React 19，免去 v5 所需的补丁包。新增 §2.4：评估 github/spec-kit 后决定移植其 **constitution** 与 **converge** 两项机制而不引入完整工具链（避免与既有 superpowers 工作流形成双真相来源），新增 R12 |
| v1.5 | 2026-08-28 | 四项待决事项全部关闭。GPU 全部改用 AutoDL，模型档位由五档并为四档（M0–M3）；**新增结论边界声明——数据安全性一维在 AutoDL 上无法取得实测证据**。修正 v1.3 的部署决策：GPU 改用 AutoDL 后，将唯一权威 PostgreSQL 置于按小时计费、可随时释放的租用实例上不再成立，改为「有状态的一切留在本机，AutoDL 只做无状态推理」；同时重新核算本机 16GB 内存预算（约 5GB，可行），并确立 Ollama 与 Dify 错峰的硬约束。通知渠道确定为企微/钉钉/飞书/邮件四渠道全实现（`NotifierPort`）。团队确定为 1 人，**总工作量由 31.5 修正为 36.0 人天**（v1.4 的 constitution 与五次 converge 共 3 人天此前漏计），日历周期 7.2 周、含缓冲约 8 周 |
| v1.6 | 2026-08-28 | 全栈版本核实（新增 [technical-stack-versions](../../TECH-STACK-VERSIONS.md)）。直接查询 PyPI、npm、GitHub Releases、endoflife.date 与 Dify 源码，非凭记忆填写。确认无依赖冲突：Python 3.14.6 的全部关键依赖均有预编译轮子（`pydantic-core`、`asyncpg`、`psycopg-binary`、`torch`、`greenlet`、`tiktoken` 均有 cp314；`ruff` 与 `playwright` 发 ABI 无关的 `py3-none-<平台>` 轮子）；Node 24.18 为 LTS 且满足 Vite 8 要求。确定 PostgreSQL 18、Dify 1.17.0、vLLM 0.28.0、pgvector 0.8.6、Ubuntu 24.04 LTS（25.04/25.10 已 EOL 不可用）。关闭 R10——Dify 源码确认支持 pgvector。新增 R14（Dify 2.0 beta 误升级）与 R15（**Dify 默认 compose 会额外起一个 PostgreSQL 容器，不干预将出现三个 PG 实例，彻底违背 pgvector 选型初衷**）。确立版本锁定策略：`uv.lock` 与 `package-lock.json` 提交进 git |
| v1.7 | 2026-08-28 | PostgreSQL 由 17 改用 **18.6**（EOL 2030-11-14，较 17 多一年）。逐组件核实：pgvector 0.8.6 明确支持（CI 矩阵含 PG18、官方 `pg18` 镜像，硬证据）；psycopg / SQLAlchemy / alembic 低风险。**两处需实测**：asyncpg 0.31.0 自实现线协议且 release notes 未声明 PG 18（v0.29 声明 PG16、v0.30 声明 PG17，v0.31 未提 PG18），Dify 1.17 的 compose 钉 `postgres:15-alpine` / `pgvector:pg16` 属未测组合。**诚实记录收益**：PG 18 新特性对本 PoC 规模基本用不上，真实收益仅 EOL 多一年——故采取「验证前置到 P1 第一天」而非「先用 17 以后再迁」，此刻验证的沉没成本为零。新增 R16 与待决 Q5（阿里云 RDS 是否提供 PG 18 未查证，若仅到 17 则本决策应推翻） |
| v1.8 | 2026-08-28 | 六位专家并行评审后重排（[合并报告](../../REVIEW-PANEL-2026-08-28.md)）。用户确认：AI 辅助中等、人天不设限、8 周非硬约束，故**全范围保留、诚实重估**，不砍场景。**总工作量 36.0 → 62.8 人天**，日历周期 15.7 周（含缓冲约 19–20 周）——同时修正原换算把人天当日历工作日的错误，改按每周 4 有效人天。**新增 P0 前置验证周**（4.3 人天）：把真正的二元风险前移，尤其「≥85% 引用准确率是否可达」的 RAG spike 与首次 AutoDL/vLLM 部署（后者原先一天工都未计，却是 P1 验收信号的前提）。八份交付文档改为随阶段增量撰写。砍范围清单降级为缓冲耗尽时的预案，并设 P3 中点为日历决策门 |
| v2.0 | 2026-08-29 | **模型档位定稿 M0–M4**。修正一处遗留：v1.9 对 §6.1 的更新因脚本在写盘前抛出断言而从未落盘，§6.1 至此仍是最初的 Qwen3-30B-A3B / 百炼版本，本次一并补齐。型号按 P0 实测改为 Qwen3.8-27B（立项 brief 原本就指定该型号，前八版用错）。M1 用 `unsloth/Qwen3.8-27B-NVFP4`，M2 用 BF16。**放弃同权重对比**——实地核查 AutoDL.Art 43 个托管模型确认无 Qwen3.8-27B，改为三组各自单目的的对比：M1 vs M2 仅量化（唯一单变量对比）、M2 vs M3 自建 vs 采购、M3 vs M4 托管横向。M3 = AutoDL.Art `Qwen3.5-397B-A17B`，M4 = DeepSeek 官方 `deepseek-v4-flash`（1M 上下文 / 384K 输出 / Tool Calls ✓）。记录 KV/token 因**混合注意力架构**（48 线性 + 16 全注意力）实为 64 KiB 而非 256 KiB。录入两档托管真实单价作为 TCO 首批数据。新增 thinking 模式四档四形状问题——三个模型均默认开启，§6.2 原断言「差异可收敛为三个配置项」不成立 |
| v2.0 | 2026-08-30 | 评审后事业部结构由 2 个增为 **3 个**：BU-A 印染 · BU-B 建陶瓷砖（集团主营，优先于卫浴）· BU-C 卫浴洁具。原「建陶卫浴事业部」拆分——瓷砖与洁具的判定维度有本质差异（瓷砖看 ΔE + 平整度 + 吸水率，洁具看**白度 W** + ΔE + 釉面针孔，且洁具有「配套件必须同注浆批」这一特有约束），合为一个 BU 会掩盖真实业务差异。产品手册相应拆为 4（岩板瓷砖）与 4b（智能洁具）。同时补充**跨 BU 消歧的六层机制**（§9.1），明确歧义应在前三层消除而非靠 Supervisor 追问兜底，其中机制① 的过滤器必须由服务端按身份注入、不接受模型或前端传入 |
| v2.1 | 2026-08-31 | **§4 数据设计补全四张缺失的表**。此前 WBS 已把它们列为 P2 任务，但设计文档从无 DDL——属「WBS 要求建、设计文档未定义」的跨文档缺口。<br>**`product` / `color`**：三张业务表只有 code 没有名称字段，用户说「白色岩板」时无从落到 `product_code`，S2–S5 四个场景全部受影响；此前产品名只存在于 RAG 产品手册中，等于要求 LLM 先检索出 code 再查 SQL，把 RAG 误差引入了本该确定的查询路径。<br>**`production_line`**：补产能后 `changeover_min` 才从死字段变为真实计算——原设计的 `plan_start`/`plan_end` 已是绝对时间，延误判定退化成两个日期比大小，而 §3.2 声称的「需计算换色调机损耗」此前是空的。<br>**`write_intent`**：三位专家独立指出 `confirm_token` 的「一次性」此前只是文字承诺，无落库位置、无唯一约束、无 TTL。新表含 `session_id` 绑定（防持令牌的他方会话完成确认）、`state` CHECK 枚举、原子消费 SQL 与三条不可省约束。<br>**`audit_log` 扩展**：补 `phase(attempt/outcome)`——两段式要写两行却无字段区分，与 P1.3.2 自相矛盾；补变更前后值与追溯目标（宪法第一条）、TTFT 与 token 计量、`retrieval_ms`；`status` 枚举补 `cancelled`。<br>同时为三张业务表补外键约束，消除 `bu_code` 各存一份且无约束导致的脏数据（如 `BU-A` 配瓷砖 `product_code`）。原 4.1.1–4.1.4 顺延为 4.1.2–4.1.5 |
| v2.2 | 2026-08-31 | 独立评审修订。**补上全项目最大缺口——身份与会话契约**（P1 详细设计 §2.5）：宪法第一条的操作者、第十条的范围上界、`write_intent` 的会话绑定三条红线都从「会话」取值，而此前无一处定义会话如何建立，若落成「前端传 `X-User-Id` 头」则三条红线同时失效。**补创建订单写端点契约**（P2 详细设计 §3.2）：此前 P2 只定义五个只读工具并声称「均为只读」，而全项目唯一写路径、唯一红线场景的请求体与幂等从未定稿。<br>**解掉宪法第十条与 S1 的正面冲突**：原表述「范围过滤器不接受模型指定」使「年度 × 区域 × 产品三维过滤」和跨 BU 用户无法实现；改为「上界由服务端注入，模型只能在内收窄、永不扩大」，且越界须显式报错而非静默降级——静默会让越权尝试无法被审计发现。<br>修正多处跨文档矛盾：`models.yaml` 的 `api_key_env` 与 `api_key: ${}` 两套 schema（前者会直接挂 CI 检查）· 评测档数三处三种说法 · `model_tier` 注释 M0..M3 · §15.3 的「M1（4090）档」· §12.2 仍要求已删的 `confirmed_at` · 宪法条数 9 → 11 |

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
- **五档**模型部署矩阵（M0–M4）与统一抽象层
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

三事业部制造集团（**建陶瓷砖为集团主营，优先级高于卫浴**）：

- **BU-A 印染事业部**：坯布 → 前处理 → 染色 → 后整理 → 成品布
- **BU-B 建陶瓷砖事业部**（主营）：压机 → 窑炉 → 抛光/施釉 → 分级 —— 瓷砖、岩板
- **BU-C 卫浴洁具事业部**：注浆成型 → 施釉 → 烧成 → 检验 —— 坐便器、面盆、浴缸

### 3.2 核心业务特征（贯穿全部设计的主线）

三个事业部共享一条**同构主线：批次色差 + 等级分选**。印染的「缸差」、瓷砖的「窑批色号差」、洁具的「注浆批差」在数据结构上完全同构，共同导致三个业务特征，这三个特征正是本 PoC 各项能力的真实需求来源：

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

#### 4.1.1 主数据（product / color / production_line）

**为什么必须有主数据表**：三张业务表只有 `product_code` / `color_code` / `line_code`，**没有任何自然语言可用的名称字段**。用户说「查一下白色岩板的库存」，工具入参却需要 `product_code='P-B-1001'`——这个映射在原设计里不存在，S2–S5 四个场景全部受影响。产品名此前只存在于 RAG 知识库的产品手册中，要求 LLM 先检索出 code 再查 SQL，既不可靠又把 RAG 误差引入了本该确定的查询路径。

```sql
CREATE TABLE product (
  product_code    VARCHAR(32) PRIMARY KEY,      -- P-A-1001 印染 / P-B-2001 瓷砖 / P-C-3001 洁具
  bu_code         VARCHAR(8)  NOT NULL,         -- BU-A 印染 / BU-B 建陶瓷砖 / BU-C 卫浴洁具
  product_name    VARCHAR(128) NOT NULL,        -- 「岩板 900×1800 素色系列」—— 自然语言检索入口
  category        VARCHAR(32) NOT NULL,         -- 坯布/印花布 · 岩板/瓷砖 · 坐便器/面盆/浴缸
  spec            VARCHAR(64) NOT NULL,         -- 展示用规格字符串
  attrs           JSONB       NOT NULL DEFAULT '{}',
    -- 结构化属性，按 BU 真实发散，硬拆成列会让两个 BU 各有一半列恒为 NULL：
    --   BU-A {"width_cm":150,"gsm":180,"yarn_count":"40S"}
    --   BU-B {"edge_mm":900,"thick_mm":9,"surface":"哑光"}
    --   BU-C {"glaze":"白釉","water_use_l":4.5}
  default_uom     VARCHAR(8)  NOT NULL,         -- 米 / 平方米 / 件 —— 与业务表 uom 对齐的权威来源
  status          VARCHAR(16) NOT NULL DEFAULT 'active',   -- active / discontinued
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON product (bu_code, category);
CREATE INDEX ON product USING gin (attrs);      -- 支撑「厚度 9mm 以上的岩板」这类数值过滤
CREATE INDEX ON product USING gin (product_name gin_trgm_ops);  -- 模糊匹配，需 pg_trgm

CREATE TABLE color (
  color_code      VARCHAR(32) NOT NULL,
  bu_code         VARCHAR(8)  NOT NULL,         -- 同一 color_code 在不同 BU 下可指不同颜色
  color_name      VARCHAR(64) NOT NULL,         -- 「米白」「藏青」—— 自然语言检索入口
  color_family    VARCHAR(32) NOT NULL,         -- 白色系/深色系/彩色系，与色差判定的例外规则挂钩
  is_dark         BOOLEAN     NOT NULL DEFAULT false,
    -- 明度 L* < 30。QC-STD-006 §2.1：深色系印染品优等品 ΔE 上限由 1.0 放宽至 1.5
  std_sample_ref  VARCHAR(64),                  -- 标准样编号；BU-C 统一为集团标准白度色板
  PRIMARY KEY (bu_code, color_code)
);

CREATE TABLE production_line (
  line_code       VARCHAR(32) PRIMARY KEY,      -- 染缸 D-01 / 窑炉 K-03 / 成型线 F-02
  bu_code         VARCHAR(8)  NOT NULL,
  line_name       VARCHAR(64) NOT NULL,
  line_type       VARCHAR(32) NOT NULL,         -- 染缸/定型机 · 压机/窑炉/抛光线 · 注浆线/隧道窑
  capacity_per_hour NUMERIC(12,3) NOT NULL,     -- 产能
  capacity_uom    VARCHAR(8)  NOT NULL,         -- 米/小时 · 平方米/小时 · 件/小时
  status          VARCHAR(16) NOT NULL DEFAULT 'running'   -- running / maintenance / idle
);
CREATE INDEX ON production_line (bu_code, line_type);
```

**`production_line` 让 `changeover_min` 从死字段变成真实计算**。原设计中 `production_plan` 的 `plan_start` / `plan_end` 已是绝对时间，延误判定退化为两个日期比大小，而 §3.2 却声称「排产需计算换色/换规格的清洗调机损耗」——该论证此前是空的。有了产能后，S5 的插单推演才成立：

```
ETA = 该线队尾 plan_end
    + (队尾 color_code ≠ 新单 color_code ? changeover_min : 0)
    + qty / capacity_per_hour
延误 = ETA > required_date
```

**外键约束**：`sales_order_line` / `inventory_batch` / `production_plan` 的 `product_code` 引用 `product`、`(bu_code, color_code)` 引用 `color`、`line_code` 引用 `production_line`。这同时消除了一类脏数据——原设计中 `bu_code` 在三张业务表里各存一份且无约束，一行数据完全可能是 `bu_code='BU-A'` 配一个瓷砖的 `product_code`。

#### 4.1.2 订单（sales_order / sales_order_line）

```sql
CREATE TABLE sales_order (
  order_no        VARCHAR(32) PRIMARY KEY,      -- 订单号 SO-2026-000123
  bu_code         VARCHAR(8)  NOT NULL,         -- BU-A 印染 / BU-B 建陶瓷砖 / BU-C 卫浴洁具
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

#### 4.1.3 库存（inventory_batch）

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

#### 4.1.4 排产（production_plan）

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

#### 4.1.5 审计表（audit_log）

```sql
CREATE TABLE audit_log (
  id              BIGSERIAL PRIMARY KEY,
  trace_id        VARCHAR(64) NOT NULL,
  session_id      VARCHAR(64) NOT NULL,
  user_id         VARCHAR(32) NOT NULL,
  phase           VARCHAR(8)  NOT NULL,         -- attempt / outcome —— 两段式写入的行区分
  action_type     VARCHAR(8)  NOT NULL,         -- read / write
  source          VARCHAR(16) NOT NULL,         -- langgraph / dify / rpa
  tool_name       VARCHAR(64),
  model_tier      VARCHAR(8),                   -- M0..M4，用于评测归因
  request_payload JSONB,                        -- 已脱敏
  response_payload JSONB,
  latency_ms      INTEGER,
  status          VARCHAR(16),                  -- success / failed / degraded / cancelled
  confirm_token   VARCHAR(64),                  -- 写操作的二次确认令牌
  target_table    VARCHAR(32),                  -- 变更目标，支撑「把 SO-xxx 的审计调出来」
  target_id       VARCHAR(64),
  before_value    JSONB,                        -- 宪法第一条要求的变更前后值；仅 outcome 行承载
  after_value     JSONB,                        -- attempt 行写在业务事务外，前值不可信
  ttft_ms         INTEGER,                      -- 首 token 延迟
  prompt_tokens   INTEGER,
  completion_tokens INTEGER,
  retrieval_ms    INTEGER,                      -- 检索耗时，与 LLM 耗时分离以便归因
  created_at      TIMESTAMPTZ DEFAULT now()
);
```


#### 4.1.6 写意图状态表（write_intent）

**为什么单独成表**：三位专家从不同角度独立指出 `confirm_token` 设计不完整——「一次性」此前只是文字承诺，没有落库位置、没有唯一约束、没有 TTL，且用「先查后写」消费在并发下必然有窗口。

```sql
CREATE TABLE write_intent (
  confirm_token   VARCHAR(64) PRIMARY KEY,      -- 服务端 secrets.token_urlsafe(32) 铸造
  session_id      VARCHAR(64) NOT NULL,         -- 消费时必须比对，防「持 token 的他方会话完成确认」
  user_id         VARCHAR(32) NOT NULL,
  trace_id        VARCHAR(64) NOT NULL,
  intent_type     VARCHAR(32) NOT NULL,         -- create_order / update_order_status / ...
  payload         JSONB       NOT NULL,         -- 用户确认过的完整载荷；执行时只取此处
  state           VARCHAR(16) NOT NULL DEFAULT 'pending'
                  CHECK (state IN ('pending','confirmed','cancelled','expired')),
  expires_at      TIMESTAMPTZ NOT NULL,         -- LangGraph interrupt 无内建超时，须自行实现
  result_ref      VARCHAR(64),                  -- 执行结果的业务主键，支撑重复提交返回原结果
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  consumed_at     TIMESTAMPTZ
);
CREATE INDEX ON write_intent (session_id, state);
CREATE INDEX ON write_intent (expires_at) WHERE state = 'pending';
```

**原子消费**（在 READ COMMITTED 下无双花：第二个 UPDATE 阻塞于行锁，第一个提交后重算谓词，`state` 已变则返回 0 行）：

```sql
UPDATE write_intent
   SET state = 'confirmed', consumed_at = now()
 WHERE confirm_token = $1
   AND session_id    = $2        -- 身份取自 Gateway 会话，不取自请求体
   AND state         = 'pending'
   AND expires_at    > now()
RETURNING payload;
```

**三条不可省的约束**：

1. **令牌绝不进入模型上下文**。若由模型在生成确认卡片时一并输出，注入指令可诱导它在后续轮次把令牌作为工具参数发出——人类根本没有被询问过。经 SSE 独立事件通道或 REST 交付前端。
2. **执行只取库中 `payload`，确认请求不得携带参数**。否则合法令牌配一份篡改过的载荷即可绕过——重放防住了，参数篡改没防。
3. **消费语义为「至多一次」**：审计独立提交，执行失败则令牌作废，不重试。

> 若业务事务用 REPEATABLE READ，上述 UPDATE 会抛序列化错误而非返回 0 行，须捕获或锁定为 READ COMMITTED。


### 4.2 RAG 知识库文档（8 份）

| # | 文档 | 类型 | 支撑场景 |
|---|---|---|---|
| 1 | 2026 年度经销商营销政策（印染事业部） | 制度 | S1 |
| 2 | 2026 年度工程集采价格政策（建陶瓷砖事业部） | 制度 | S1 |
| 3 | 产品手册：功能性面料系列 | 手册 | S1 S2 |
| 4 | 产品手册：岩板瓷砖系列（BU-B） | 手册 | S1 S2 |
| 4b | 产品手册：智能洁具系列（BU-C） | 手册 | S1 S2 |
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
        │  M0 本机Ollama   │ M1 AutoDL 4090 (SSH)      │
        │  M2 AutoDL A100  │ M3 AutoDL.Art · M4 DeepSeek │
        └──────────────────────────────────────────────┘
```

### 5.1 关键选型与取舍

| 选型 | 决定 | 理由 | 备选 |
|---|---|---|---|
| 向量后端 | **pgvector（由 Dify 托管）** | 检索在 Dify 内，向量库归 Dify 管而非应用层。PostgreSQL 本已必须部署（业务数据 + 审计 + checkpointer），复用即少一个组件、少一套备份与监控体系；且向量与业务表同库，一致性对账可直接用 SQL join | Qdrant / Milvus，改 Dify `VECTOR_STORE` 并重新索引即可切换（详见 §8.3） |
| 检索出口 | **`VectorStorePort` 接口** | 真正需要抽象的不是向量库（Dify 已替我们抽象），而是「是否继续用 Dify 检索」这个决策点。本期只实现读侧，写入权归 Dify 独占（§8.4） | — |
| 请求入口 | **FastAPI Gateway** | 统一鉴权、限流、审计埋点与分流。LangGraph 是网关后的执行器之一，不是入口本身 | — |
| 前端 | **React 19 + Vite + Ant Design 6** | 团队现有 React 栈；AntD 的 Modal / Table / Card 直接支撑二次确认弹窗、审计追溯与引用卡片 | Streamlit（快 2 天，观感偏糙） |
| 通知渠道 | **`NotifierPort` + 四适配器** | 企微 / 钉钉 / 飞书均支持群机器人 webhook，只需一个 webhook 地址、无需企业应用审批；邮件走 SMTP。四者统一接口、配置化启用 | — |
| 版本锁定 | **`uv.lock` + `package-lock.json` 提交进 git** | 单人项目无第二双眼睛复核环境差异，锁文件是环境可复现的唯一保障；全部组件版本见[技术栈版本矩阵](../../TECH-STACK-VERSIONS.md) | — |
| Agent 编排 | **LangGraph** | 代码即流程，天然可 git diff、可单测、可进 CI，契合「git 管理全生命周期」约束 | — |
| 流程编排 | **Dify** | 业务人员可自助迭代的可视化层 | n8n |
| Agent 拓扑 | **Supervisor + 五子图** | 五场景边界天然清晰；子图各持槽位状态，避免全局 state 污染 | 单一大图（可推翻项） |

### 5.2 部署拓扑

| 位置 | 承载 | 常驻性 |
|---|---|---|
| **本机 macOS（16GB）** | PostgreSQL + pgvector（**唯一权威实例**）、Dify 全套容器、Embedding / Rerank、FastAPI + LangGraph、React dev server | 常驻 |
| **AutoDL GPU 实例** | **仅 vLLM 推理**（M1 4090 24G / M2 A100 80G），经 SSH 端口转发接入 | **按需开关机** |
| **AutoDL.Art 托管 API** | M3 `Qwen3.5-397B-A17B`，OpenAI 兼容 HTTP | 托管 |
| **DeepSeek 官方 API** | M4 `deepseek-v4-flash`，OpenAI 兼容 HTTP | 托管 |
| 本机 Ollama | M0，qwen3:8b，开发 / CI 用 | 按需，与 Dify 错峰 |

#### 为什么权威数据库不能放 AutoDL

v1.3 曾将 PostgreSQL 置于「GPU 服务器」。GPU 全部改用 AutoDL 后该决策不再成立——**AutoDL 是按小时计费的租用实例，可随时释放**，把唯一权威数据库放在其上，等于将业务数据、审计日志与向量索引一并托付给一台临时机器。审计日志尤其不可丢失（宪法第一条）。

因此改为：**有状态的一切留在本机，AutoDL 只做无状态推理**。AutoDL 实例可随时销毁重建而不损失任何数据，「只在评测时开机」的省钱策略也因此成立。

#### 本机 16GB 内存可行性核算

v1.1 曾判断「本机 16GB 扛不住 Dify 全家桶 + PostgreSQL」。该判断偏保守，且当时假设向量库是独立组件。重新核算：

| 组件 | 常驻内存（估） |
|---|---|
| Dify 精简部署（不启 sandbox、单 worker） | ~3.5 GB |
| PostgreSQL + pgvector | ~0.5 GB |
| FastAPI + LangGraph | ~0.5 GB |
| React dev server | ~0.5 GB |
| **小计** | **~5 GB** |
| Ollama qwen3:8b（M0，按需） | +6 GB |

**硬约束**：M0（Ollama）与 Dify 不得长时间同时运行，须错峰。此约束写入部署文档与 Makefile（`make dev` 与 `make dev-m0` 互斥）。若 P3 实测内存吃紧，退路是将 Dify 迁至一台常驻低配云主机（2 核 4G 即可），**而非迁回 AutoDL**。

---

## 6. 模型部署矩阵与抽象层

### 6.1 五档部署矩阵

| 档位 | 部署形态 | 模型 | 角色 |
|---|---|---|---|
| **M0** | 本机 Ollama | `qwen3:8b` | 单测 / CI 跑通链路，**不进对比结论** |
| **M1** | AutoDL **5090 32G** + vLLM（SSH 隧道） | **`unsloth/Qwen3.8-27B-NVFP4`** | **自建 · 经济档** |
| **M2** | AutoDL **A100 80G** + vLLM（SSH 隧道） | **`Qwen/Qwen3.8-27B`（BF16）** | **自建 · 质量上限** |
| **M3** | **AutoDL.Art 托管 API** | **`Qwen3.5-397B-A17B`** | **采购 · 同厂商** |
| **M4** | **DeepSeek 官方 API** | **`deepseek-v4-flash`** | **采购 · 跨厂商** |

#### 型号修正（P0 实测）

**立项 brief 指定的是「Qwen3.8 系列 27B」，该型号真实存在，本设计前八版用错了型号。** v1.0 曾断言其不存在并改用 Qwen3-32B / Qwen3-30B-A3B，理由是知识截止于 2026-05；R1 风险登记了这一条并跨越八个版本，直到 P0 才执行核实。

**实测规格**（`config.json` + safetensors 分片体积）：

| 变体 | 权重 | 层结构 | 原生 ctx |
|---|---|---|---|
| `Qwen/Qwen3.8-27B` | **51.7 GiB** | 64 层 = **48 线性注意力 + 16 全注意力** | 262144 |
| `unsloth/Qwen3.8-27B-NVFP4` | **21.8 GiB** | 同上 | 262144 |

架构名 `Qwen3_5ForConditionalGeneration`（多模态族），**vLLM 0.28.0 registry 已注册**。

**混合注意力使 KV/token = 16 × 4 kv_heads × 256 head_dim × 2 × 2 = 64 KiB**（不是按 64 层算的 256 KiB）。据此 M1 的 KV 容量 75K token、M2 为 272K，需求基线 35K，两档均大幅超标。完整测算见 [AutoDL 规格 v1.2](../../AUTODL-SPEC.md)。

#### 三组对比，各自回答一个独立问题

| 对比 | 变量 | 回答什么 |
|---|---|---|
| **M1 vs M2** | **仅量化**（同为 Qwen3.8-27B） | 量化损失值不值——全设计中唯一的单变量对比 |
| **M2 vs M3** | 部署方式 + 模型规模 | **自建 vs 采购**：自建受显存约束只能跑 27B，采购能用上 397B |
| **M3 vs M4** | 供应商 + 模型 | 托管方案的横向选型 |

#### 为什么放弃「同权重对比」

原设计的核心对照组是「同系列权重、同参数规格，变量仅剩部署方式」。**该方案已不可得**——实地核查 AutoDL.Art 托管模型市场的 43 个模型，其中没有 Qwen3.8-27B；最接近的 `Qwen3.5-397B-A17B` 是 397B 总参 / 17B 激活的 MoE，与自建的 27B 稠密模型规模相差约 15 倍。

**但这反而更贴近真实决策。** 没有企业会在「自建 Qwen」与「买同一家的同一个 Qwen」之间纠结；真实选择是「自建一个显存装得下的」对「采购一个自己根本装不下的」。M2 vs M3 恰好是这个问题。

**代价必须写入报告**：M2 vs M3 的准确率差异**不可归因于部署方式**，模型、供应商、部署三个变量同时改变。报告须**分场景**给出准确率，使读者能自行判断差距来自模型能力还是部署形态。

> ⚠️ **结论边界（必须写入 PoC 报告）**：AutoDL 是第三方 GPU 租用平台，数据出企业边界。故本次 PoC 验证的是**「自建 vs 采购」的技术与成本差异**，**而非「私有化 vs 上云」的数据安全差异**。brief 所列四个对比维度中，**数据安全性一维无法取得实测证据**，报告中只能给出架构层面的分析，真正的私有化结论需在企业自有机房复现方才成立。

#### 成本基线（P5 TCO 模型的第一批真实数据）

按 P5 评测量（98 条用例 × 3 次运行 = 294 次调用，每次约 3500 输入 + 500 输出 token）：

| 档 | 单价（输入 / 输出，每 M token） | 全量评测成本 |
|---|---|---|
| M3 `Qwen3.5-397B-A17B` | ¥0.72 / ¥4.32（会员价） | **¥1.38** |
| M4 `deepseek-v4-flash` | ¥2.10 / ¥6.30 | **¥3.09** |

**两个托管档跑完全部评测合计不足 ¥5**，而 M1/M2 的 GPU 按小时租用，数十小时下来相差两个数量级。但须同时说明：**托管成本随调用量线性增长，自建的边际成本趋近于零，交叉点在哪里正是 TCO 模型要回答的**。

### 6.2 抽象层设计

四档远端服务均为 OpenAI 兼容接口，但差异**不能**收敛为 base_url / model / api_key 三项——见下方 thinking 模式问题。

```yaml
# config/models.yaml
tiers:
  M0: { base_url: "http://127.0.0.1:11434/v1", model: "qwen3:8b",
        api_key: null,          # Ollama 无需鉴权                 extra_body: {}   # 空是查证四种方式后确认无解，非配置遗漏（P0 实测） }
  M1: { base_url: "http://127.0.0.1:18001/v1", model: "Qwen3.8-27B-NVFP4",
        api_key: "${VLLM_API_KEY}",         # AutoDL 5090, ssh -L 18001
        extra_body: { chat_template_kwargs: { enable_thinking: false } } }
  M2: { base_url: "http://127.0.0.1:18002/v1", model: "Qwen3.8-27B",
        api_key: "${VLLM_API_KEY}",         # AutoDL A100, ssh -L 18002
        extra_body: { chat_template_kwargs: { enable_thinking: false } } }
  M3: { base_url: "<AutoDL.Art 控制台『令牌管理』获取>", model: "Qwen3.5-397B-A17B",
        api_key: "${AUTODL_ART_API_KEY}",   extra_body: { }   # P1 实测确认关闭方式 }
  M4: { base_url: "https://api.deepseek.com", model: "deepseek-v4-flash",
        api_key: "${DEEPSEEK_API_KEY}",     extra_body: { }   # P1 实测确认关闭方式 }
```

**M4 实测能力**：上下文 **1M**、最大输出 **384K**、**Tool Calls ✓**、JSON Output ✓。

#### ⚠️ thinking 模式：四档四种开关形状

`Qwen3.8`、`Qwen3.5-397B-A17B` 与 `deepseek-v4-flash` **均默认开启思考模式**，思考 token 常占输出多数，会直接击穿 §14 的延迟指标。而关闭开关在各档形状不同：

| 档 | 关闭方式 |
|---|---|
| M0 Ollama | 请求体顶层 `think: false` |
| M1 / M2 vLLM | `extra_body.chat_template_kwargs.enable_thinking = false` |
| M3 AutoDL.Art | 遵循 Qwen 约定，**P1 实测确认** |
| M4 DeepSeek | 官方文档「Thinking Mode」章节所述参数，**P1 实测确认** |

**thinking 开关状态必须作为评测的显式变量记录**，否则跨档位的延迟数字不可比。

`LLMGateway` 统一职责：档位路由、超时与重试、token 计量、延迟埋点、失败降级、**将 model_tier 写入每条审计记录**（评测归因的前提）。

**AutoDL 连接管理**：AutoDL 实例通常不提供公网 IP，需通过 SSH 端口转发将远端 vLLM 端口映射到本地——M1 与 M2 各占一个本地端口（`ssh -L 18001:localhost:8000` / `ssh -L 18002:localhost:8000`），两档可并存以便同一测试集连续跑完。《AutoDL 部署配置参数设置指南》需覆盖：实例规格选择、vLLM 启动参数（`--max-model-len`、`--gpu-memory-utilization`、量化方式）、隧道保活与断线重连、按需开关机的省钱操作流程。

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

> **已核实（2026-08-28）**：Dify 源码 `api/core/rag/datasource/vdb/vector_type.py` 共枚举 32 种向量后端，`VectorType.PGVECTOR` 在列，Qdrant 与 Milvus 亦在列。详见[技术栈版本矩阵](../../TECH-STACK-VERSIONS.md) §7。
>
> ⚠️ **随之发现一个部署陷阱**：Dify 的 `docker-compose.yaml` 中向量库 `pgvector` 是**独立于 Dify 元数据库 `db_postgres` 的 service**，各带 compose profile。若按默认部署，加上我们的业务库将出现**三个 PostgreSQL 实例**，彻底违背选用 pgvector 的初衷。处置方式是配置 Dify 的 `DB_HOST` 与 `PGVECTOR_HOST` 均指向我们那一个实例的 `dify` 库，并从 `COMPOSE_PROFILES` 中移除 `postgresql` 与 `pgvector`。详见版本矩阵 §6，此项列入 P1 实施细节与 converge 检查。

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

> **BU 消歧示例**：用户问「查一下白色的库存」，须消歧到**布 / 砖 / 洁具**三者之一——三个事业部的色号体系与库存维度完全不同（印染按缸号、建陶按窑批色档、洁具按注浆批 + 白度）。
>
> **消歧不靠 Supervisor 追问兜底**，而是六层机制递进，前三层让歧义大部分不产生：① 身份注入（服务端按 `default_bu` 强制注入过滤，不接受模型或前端传入——宪法第十条）· ② 实体推断（产品词自动定 BU，复用 L1 规则表）· ③ 会话粘性 · ④ 召回校验 · ⑤ 按 BU 拆知识库文件（治本，因 Dify 元数据过滤是文档级的）· ⑥ 答案强制标注适用事业部。详见 [P0-6 方案 §7](../../P0-6-RAG-SPIKE-PLAN.md)。

### 9.2 三级意图路由

Supervisor 若每轮都调主模型做意图识别，将额外增加 0.5–2s 延迟，直接侵蚀延迟指标。改为三级路由，逐级下沉：

| 级别 | 手段 | 延迟 | 预期覆盖 |
|---|---|---|---|
| L1 | **规则前置**：订单号正则 `SO-\d{4}-\d{6}`、「库存 / 排产 / 政策」等强关键词命中 | ~0ms | 40–60% |
| L2 | **小模型分类**：M0 档 Qwen3-8B 或 embedding + 分类头 | ~100–200ms | 大部分剩余 |
| L3 | **主模型兜底**：仅真正模糊的请求走 M1–M3 + function calling | 0.5–2s | 少数 |

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
| **vLLM 推理并发** | 按 P0 实测的混合注意力架构（KV/token 64 KiB）重算：5090 32G 跑 NVFP4 的 KV 容量 75K token、A100 80G 跑 BF16 为 272K，10 并发 × 3.5K = 35K 需求下两档均有余量。实际并发上限由计算而非 KV 决定，须 P5 压测 | 硬上限，本期不试图突破；压测据此标定 |
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
| 单轮响应平均延迟 | M2 P50 ≤5s / M1 P50 ≤4s / M3·M4 P50 ≤3s | 流式首 token 与完整响应分别统计 |
| 边界情况处理 | 全部正确 | 缺参追问、模糊意图、BU 消歧、API 失败降级 |
| **并发承载** | **≤10 并发会话不劣化** | P5 做 10 并发压测，M1 / M2 两档分别测；未达标则作为报告结论之一 |
| 双部署对比结论 | 出数 | 准确率、延迟、成本、数据安全性四维 |

**测试集构成**：S1–S5 各 6 条常规用例；边界 10 条覆盖缺参、模糊意图、跨 BU 歧义、超范围提问、API 超时、库存不足、幂等重复提交、追问轮次超限、拒绝确认、越权查询。

**评测维度归因**：每条用例在 **M1/M2/M3/M4 四档**各跑一次，结果按 `model_tier` 归因，输出对比矩阵。M0 仅用于 CI 冒烟，不进对比结论。

---

## 15. 分阶段实施计划

> **v1.8 重排依据**：六位专家评审（[合并报告](../../REVIEW-PANEL-2026-08-28.md)）指出 36 人天从未做过自下而上分解——工作量轨迹 30→31→31.5→36，每次变动都是「补记漏项」，而任务级 WBS 本该用于估算却被排在 P5 产出。用户已明确：**AI 辅助程度中等、人天预算不设限、8 周非硬约束**。因此本版按**全范围保留 + 诚实重估**重排，不砍场景。

### 15.0 新增 P0：前置验证周

多位专家独立建议把不确定性最高的项前移。原设计只前置了 PG18（其退路成本极低），却把真正的二元风险——**「≥85% 引用准确率是否可达」**——留到 P5 才测量，而补救手段全在 P3 上游。

| 任务 | 人天 | 失败后果 |
|---|---|---|
| asyncpg 0.31.0 × PG 18.6 连接与类型往返 | 0.25 | 切 psycopg3 async，PG 不退 |
| pgvector HNSW + Dify 建库→索引→检索（走通向量路径） | 0.5 | 整体退回 PG 17 |
| Dify 指向外部 PG + `COMPOSE_PROFILES` 字面量覆写 + 13 容器 `docker stats` 实测 | 0.5 | 内存预算重做，Dify 可能需迁云主机 |
| AutoDL 开机 → SSH 隧道 → vLLM 起（**含 `--enable-auto-tool-choice --tool-call-parser hermes`，验收须返回结构化 `tool_calls`**） | 1.5 | P1 验收信号不成立 |
| **RAG spike**：1 份最难文档（《色差与等级判定标准》）进 Dify，问 5 题人工核对引用可溯源 | 0.5 | **引用不可溯源则 §8 检索路径需重做** |
| 前端栈 spike：Vite 8 + React 19 + antd 6 + TS 7 脚手架 + 一个流式组件 | 0.25 | TS 7 回退 5.x |
| ~~R1 型号核实~~ | 0.2 | ✅ **已完成**：发现前八版型号用错，改用 Qwen3.8-27B；托管侧无同权重，已放弃同权重对比 |
| 任务级 WBS（≤0.5 人天粒度，从 P5 前移）+ 外部依赖表 | 0.6 | — |
| **小计** | **4.3** | |

### 15.1 各阶段

| 阶段 | 原 | 新 | 主要增量 |
|---|---|---|---|
| **P0 前置验证** | — | **5.3** | 全新增；含后补的 P0-9 多模态 embedding 1.0（低优先级待办） |
| **P1 地基** | 4.0 | **8.65** | 审计基座含变更前后值与两段式写入、`REVOKE UPDATE/DELETE` 防篡改；CI 收敛为不含 LLM 的宪法机械检查（0.75）；`make backup` + **恢复演练**（0.35）；宪法修订（第十条不可信输入 + 版本同步） |
| **P2 数据 + API** | 5.5 | **9.0** | **产品/色号主数据表、`production_line` 产能表、排产关联到订单行、`stage_seq`、`uom`、`batch_policy`、`attrs JSONB`**；fixture 规格 + 固定种子 + 断言校验（0.5）；API 契约含错误模型与分页（0.5）；权限最小模型（0.5）；**预注册评分细则**（0.5）；8 份文档的标识体系与数据同步设计 |
| **P3 RAG + Agent** | 10.5 | **15.5** | `interrupt()` 拓扑拆分 + `write_intent` 表（1.0）；L2 改零训练 embedding 最近邻 + margin 判据 + 意图粘性（1.2）；分档追问 + 槽位修正（1.3）；Prompt Injection 不变量声明 + 定界符（0.5）；**前端只做对话流 + 二次确认弹窗**，其余组件移 P5 |
| **P4 Workflow + RPA** | 9.0 | **10.3** | D12 确认改用 `interrupt()` 并**移出降级关键路径**；`trace_id` 写入 ERP 表单使两侧日志可 join；attempt-first 审计 |
| **P5 评测 + 交付物** | 7.0 | **16.95** | RAG 独立 50 条评测集 + 注入 8 条 + 边界扩充（2.0）；失败类型归因（意图/槽位/工具格式/业务逻辑）；**3 档 × 3 次运行**；LLM-as-judge 双判卷 + 人机一致率（1.5）；**参数化 TCO 模型**（0.75）；真实系统对接工作量估算 + 场景价值排序（1.0）；前端剩余四组件（2.0）；业务方真人试用（0.3） |
| **合计** | **36.0** | **62.8** | |

各阶段末仍含 0.5 人天 converge 检查。**八份交付文档改为随阶段增量撰写**（部署运维在 P1 环境搭完时写、API 文档在 P2 由 OpenAPI 生成、落地手册每阶段追加一节），把 P5 的 4.5 人天悬崖摊平，并让里程碑一真正成为可交付兜底。

### 15.2 工期换算

原换算「36 人天 ÷ 5 天/周 = 7.2 周」把**人天当成了日历工作日**，隐含 100% 投入效率。单人项目计入沟通、环境折腾、上下文切换后，现实是**每周 4 个有效人天**。

| 口径 | 结果 |
|---|---|
| 62.8 人天 ÷ 5 天/周（旧口径） | 12.6 周 |
| 65.70 人天 ÷ **4 有效天/周**（现实口径） | **16.4 周** |
| + 25% 缓冲（匹配未测组合数量，集中持有不铺进各阶段） | **约 20 周** |

**缓冲动用规则**：缓冲集中持有，不预先分配到阶段。设 **P3 中点为日历决策门**——到点未达既定进度即执行 §15.3 的砍范围清单，由用户决策，不由实施者临场决定。

### 15.3 砍范围清单（预案，当前不执行）

#### 🔴 D1 的日历死线与自动触发

**D1（AutoDL 实例 + SSH 凭据）原定死线「P1 开工前」已失效**——P1 出口判据于 2026-08-30 改为 M0/M3/M4 三档后，该死线不再触发任何动作，D1 可以一路静默滑到 P5 才被发现缺失。届时 M1/M2 无从补做，「自建 vs 采购」这个 PoC 的核心结论直接作废。

**新死线：P2 结束（约第 6 周）。** 选这个点的理由是**失败后还有回旋余地**——P3 尚未开工，砍掉自建档对 P3/P4 毫无影响，只影响 P5 的评测范围，且有足够时间调整报告的结论声明。排到 P3 中点就晚了，排到 P5 则等于没有死线。

**到点未到位则自动触发**（不再另行决策）：

| 到位情况 | 触发 | 对报告的影响 |
|---|---|---|
| 两种卡都到位 | 无 | 按原计划 |
| **仅一种卡** | 执行砍范围清单第 5 条，保留 A100（M2）—— 它是「准确率上限基准」，比经济档更不可替代 | 失去量化损失的对照（M1 vs M2），保留自建 vs 采购 |
| **完全未到位** | **自建侧整体作废**：P0-7（1.5）、P5.3 的一半、P5.5（0.7）、TCO 的自建曲线全部移出范围，约省 4 人天 | 🔴 **「本地/云端双方案对比」降级为「不同托管方案横向对比」**，报告须在结论页显式声明该维度无实测证据 |

最后一行是**立项 brief 明文要求的交付物之一**（「输出本地/云端双模型方案对比结论与选型建议」）。降级不是可以悄悄发生的事——须在 P2 结束时以书面形式告知立项方，与 §6.1 已声明的「数据安全性维度无实测证据」并列。

**责任归属**：D1 是外部依赖，其瓶颈是资源与前置周期而非实施方工时。**实施方的义务是到点如实报告状态并触发上表，不是自行承担延误。**



用户已确认不砍范围。以下按「决策价值 / 成本」排序，仅在缓冲耗尽触发决策门时启用，可砍出约 7.5 人天：

1. 《向量数据纳管服务设计》11 节 → 1 页附录（省 1–1.5，决策影响为零）
2. P5 的 pgvector→Qdrant 切换验证与全量重跑（省 1.0）
3. 通知渠道 4 → 1（省 1.0，同时消掉三个 IM 平台的外部凭据依赖）
4. Dify 12 项 → 6 项，保留 D1/D2/D3/D5/D7/D12（省 1.5–2.0）
5. M1（5090）档（省 1.0–1.5，成本数据点可由 M2 吞吐 + 报价推算）
6. 模拟遗留 ERP 降为单页表单（省 1.5，保留降级链路与审计留痕）
7. 10 并发压测 → 3 并发冒烟 + 书面外推（省 0.5）

**不可砍**：知识文档与模拟数据的真实感 · 写路径的二次确认+审计+幂等 · React 对话界面 · 评测严谨性 · 从零落地手册。

### 15.4 分支策略

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
| R4 | ~~托管档 API Key 尚未提供~~ | — | ✅ **已关闭**（v2.0）：AutoDL.Art 与 DeepSeek 官方两个 Key 均由用户确认具备，按宪法第七条注入 `.env` |
| R5 | AutoDL 无公网 IP，SSH 隧道易断 | M3 数据不稳 | 隧道保活 + 自动重连；M3 为补充数据点，非核心对照组 |
| R6 | Dify 检索配置不进 git | 违反 git 全生命周期约束 | `dify_snapshot.py` 配置快照版本化（§8.2） |
| R7 | 十二项 Dify 场景范围较大 | 工期超支 | 里程碑一为完整交付切点，中期可据反馈砍 P4/P5 范围 |
| R8 | **三 BU** 使数据与文档工作量放大 | 工期超支 | 共用同构数据模型，仅属性字段差异化；卫浴（BU-C）为次要优先级，语料与数据量按 印染:建陶:卫浴 ≈ 4:5:1 分配 |
| R9 | 工具函数中混入同步阻塞调用 | 并发直接塌方，延迟指标失真 | 全链路 async 强制约束 + lint 规则拦截；压测作为兜底检出手段 |
| R10 | ~~Dify 对 pgvector 的支持未核实~~ | — | ✅ **已关闭**（v1.6）：源码 `VectorType.PGVECTOR` 确认支持，Qdrant / Milvus 同在列，切换验证可行。详见[技术栈版本矩阵](../../TECH-STACK-VERSIONS.md) §7 |
| R16 | asyncpg 0.31.0 未声明支持 PG 18（其惯例是逐版本明确声明），Dify 1.17 的测试矩阵亦只到 PG 15/16 | 数据访问层或 Dify 无法工作 | P1 第一天前置验证（版本矩阵 §9 清单）；asyncpg 失败可切 psycopg3 async，改一行连接串；整体失败则退回 PG 17 |
| R14 | Dify 2.0 仍处 beta（2.0.0-beta.2），8 周周期内可能转正，误升级将引入破坏性变更 | 环境不可复现、返工 | 锁定 **Dify 1.17.0**，compose 中固定镜像 tag 不用 `latest`；每阶段 converge 检查确认版本未漂移 |
| R15 | Dify 默认 compose 额外起一个 PostgreSQL 容器供 pgvector 使用，若未干预将出现三个 PG 实例 | 彻底违背 pgvector 选型初衷（少组件、统一运维、SQL 可对账） | 配置指向外部 PostgreSQL 并移除相应 compose profile（版本矩阵 §6）；P1 converge 检查中验证实例数为一 |
| R13 | AutoDL 实例按小时计费且可随时释放，SSH 隧道中断或实例重建将打断评测 | M1/M2 档评测数据不完整 | 隧道保活与断线重连；评测脚本支持断点续跑；每次评测前校验各档连通性；有状态数据全部留在本机（§5.2），实例可随意重建 |
| R12 | 750 行设计文档中的硬约束散落各章，实现者（人或 agent）漏看单条约束 | 审计缺失、双写、同步阻塞等红线被击穿 | 抽取为 `docs/CONSTITUTION.md` 九条原则（§2.4）；每模块动工前逐条核对，每阶段末做收敛检查 |
| R11 | 后续有人绕过 Dify 直接写向量库 | 引用溯源断裂，击穿 ≥85% 引用准确率指标 | §8.4 写入权模型写入设计文档并在代码中以接口分层强制；`VectorStorePort` 写侧本期不提供实现。**pgvector 下该风险更高**——向量表与业务表同库，SQL 可直达，须以数据库账号权限隔离：应用账号对 `dify` 库只读 |

---

## 17. 待决事项

截至 v1.7，Q1–Q4 已关闭；**Q5 为 v1.7 新增，需用户确认**（不阻塞 P1 开工，但影响 PG 版本决策是否成立）。

| # | 事项 | 处置 |
|---|---|---|
| Q1 | 托管档 API Key | ✅ **已关闭**（v2.0）：`AUTODL_ART_API_KEY` 与 `DEEPSEEK_API_KEY` 均由用户确认具备，按宪法第七条自行注入 `.env`，不进 git |
| Q2 | GPU 部署形态 | ✅ **已关闭**：全部采用 AutoDL，交付《AutoDL 部署配置参数设置指南》；SSH 凭据由用户注入 `.env` |
| Q3 | 团队规模 | ✅ **已关闭**：1 人，全串行，日历周期 7.2 周（含缓冲约 8 周），**不砍范围** |
| Q4 | 通知渠道选型 | ✅ **已关闭**：企微 / 钉钉 / 飞书 / 邮件四渠道全部预置，统一 `NotifierPort` 接口 |
| Q5 | **阿里云 RDS 是否提供 PostgreSQL 18** | ❓ **待确认**：若目标 RDS 仅支持到 17，则以 18 开发反而制造迁移障碍，PG 18 决策应予推翻。需用户查证 |
