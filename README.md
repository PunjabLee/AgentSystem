# AgentSystem — 企业级智能问答与任务执行系统 PoC

以「订单全生命周期」为主线，打通 **自然语言问答 → 业务查询 → 任务执行** 闭环的概念验证项目。

业务域：**三事业部**制造集团 —— BU-A 印染 · BU-B 建陶瓷砖（主营）· BU-C 卫浴洁具。

## 要验证的四项能力

1. 自然语言完成业务查询与**写操作**
2. 静态知识（营销政策等文档）与动态业务数据（库存、订单、排产）**融合问答**
3. LLM 与业务系统 API 双向打通（读 + 写）
4. RAG、Workflow、RPA 三条技术路线的集成与协同

五个主线场景：S1 查营销政策 · S2 查库存 · **S3 创建订单（写）** · S4 查订单进展 ·
S5 查排产计划（含「某订单延期影响哪些下游」的联动分析）。

## 状态

| 阶段 | 状态 |
|---|---|
| P0 前置验证 | ✅ 完成 |
| P1 地基 | ✅ 完成 —— 三条出口判据机械判定全 PASS（`make p1-exit`） |
| **P2 数据 + API** | 🚧 进行中 —— 建表、fixture、响应包络与权限注入已落地 |
| P3 RAG + Agent · P4 Workflow + RPA · P5 评测与交付 | 待开始 |

**外部依赖**：D1（AutoDL 实例）未到位，M1/M2 两档模型至今未验证，死线为 P2 结束。

## 文档导航

| 文档 | 位置 |
|---|---|
| **给 AI 协作者的上手指引** | [CLAUDE.md](CLAUDE.md) |
| **项目宪法**（11 条不可协商原则 + 附则） | [docs/CONSTITUTION.md](docs/CONSTITUTION.md) |
| 总体架构设计 | [docs/superpowers/specs/](docs/superpowers/specs/) |
| 任务分解与人天 | [docs/WBS.md](docs/WBS.md) |
| 各阶段详细设计（跨阶段契约） | [docs/design/](docs/design/) |
| 未完成工作清单 | [docs/OPEN-ITEMS.md](docs/OPEN-ITEMS.md) |
| 评审裁决报告 | [docs/REVIEW-PANEL-2026-09-03.md](docs/REVIEW-PANEL-2026-09-03.md) |
| 🔒 预注册评分细则（已冻结） | [docs/eval/SCORING-RUBRIC.md](docs/eval/SCORING-RUBRIC.md) |
| 部署与运维 | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |

## 快速开始

```bash
cp .env.example .env     # 填入数据库口令与模型 API key
make check               # lint + 测试
make seed                # 灌模拟数据并跑断言校验
make chat                # 模型连通性冒烟
```

## 协作约定

分支策略与 `main` 保护规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。
`main` 受保护，**直接推送会被拒** —— 走分支 + PR，CI 必过。
