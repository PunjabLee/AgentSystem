# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目性质

制造业集团（三事业部：BU-A 印染 · BU-B 建陶瓷砖 · BU-C 卫浴洁具）的**企业 AI 应用 PoC**，
以「订单全生命周期」为主线打通「自然语言问答 → 业务查询 → 任务执行」。
团队 **1 人**（用户 + AI 辅助）。全中文项目：文档、注释、提交信息一律中文。

**这是 PoC，不是生产系统** —— 不要提 WORM 存储、完整 RBAC、分库分表这类生产级要求。
**唯一例外：审计与安全是立项时的硬承诺，不打折。**

## 三份必读文档

动手前先读，它们的约束高于一般工程惯例：

| 文档 | 作用 |
|---|---|
| `docs/CONSTITUTION.md` | **11 条不可协商原则** + 两条附则。每条都有「如何验证」，多数已有机械检查 |
| `docs/WBS.md` | 阶段任务分解。**任务描述里的并列项容易漏做** —— 按描述逐句核对，不要按标题 |
| `docs/design/P{1,2}-DETAILED-DESIGN.md` | 跨阶段契约（接口签名、状态机、错误模型、实现顺序） |

其余：`docs/OPEN-ITEMS.md`（未完成清单）· `docs/REVIEW-PANEL-*.md`（评审裁决）·
`docs/eval/SCORING-RUBRIC.md`（🔒 已冻结的评分细则，只能追加勘误）。

## 常用命令

```bash
make check          # lint + test，提交前必跑
make test           # pytest（不含 LLM 调用）
make seed           # 清空并重灌模拟数据 + 断言校验
make seed-check     # 只跑断言校验，不动数据
make chat           # 模型冒烟；TIER=M4 指定单档
make audit-health   # 查有 attempt 无 outcome 的写操作
make p1-exit        # P1 三条出口判据的机械判定

uv run pytest tests/test_write_intent.py::test_concurrent_consume_exactly_one_wins  # 单个用例
uv run pytest tests/test_permission_scope.py -q                                      # 单个文件
```

**迁移分两种，不可混用：**

```bash
make migrate        # 常规表
make migrate-audit  # audit_log 结构变更：解锁事件触发器 → 迁移 → 重新上锁
```

## 架构中跨文件的不变量

以下每条都是读单个文件看不出来的，且违反后**不会报错，只会静默出错**。

### 审计：两段式 + 独立池 + 三层防篡改

`audit/writer.py` 先写 `attempt` 行（**独立连接池、独立提交**），业务事务执行，再写 `outcome` 行。
理由：业务事务中途崩溃时 attempt 已落库，「有人试过但结果不明」可被 `make audit-health` 检出。

- **审计池必须独立**（业务 5 / 审计 3，均 `max_overflow=0`）。实测共池在池满时**死锁**：
  并发 5 时 0/5 成功全部超时，独立池 5/5 成功耗时 0.11s。
- `audit_log` 仅追加。三层防护：`REVOKE` + 行/TRUNCATE 触发器 + **`ddl_command_end` 事件触发器**。
  第三层拦住表属主与超级用户的 `ALTER TABLE ... DISABLE TRIGGER` —— 而 Alembic 正是以属主身份运行。
- 因此 **`audit_log` 只能 fix-forward，禁止 `alembic downgrade`**（宪法一之附则二）。
  `DROP COLUMN` 与 `ADD COLUMN` 同属 `command_tag = 'ALTER TABLE'`，两个方向都被拦。

### 权限：注入在 db 层，失败关闭

`db/scope.py` 的 `scoped_select()` 是业务表查询的**唯一入口**。端点函数签名里根本没有
`bu_codes` / `regions`，想漏都漏不掉。无 `RequestContext` 时**抛错而非不过滤**。

`tests/test_permission_scope.py::test_business_tables_are_never_selected_directly`
会检出任何直接 `select(SalesOrder)` 的写法。

**越界必须显式报错，不得静默返回空** —— 空结果与「无权限」在用户看来一样。

### 不建外键（设计文档 §4.0 决策）

九张业务表**一律无外键**。三项补偿手段是该决策成立的**前提**而非建议：

1. 种子生成器按拓扑序生成，且断言孤儿行数为 0（`fixtures/assertions.py`）
2. 只读端点的 JOIN 一律 `INNER JOIN`
3. 写端点插入订单行前校验 `order_no` 存在

接入真实 ERP 数据时本决策必须重审。

### LangGraph：含 `interrupt()` 的节点会从头重放

实测（`tests/test_langgraph_semantics.py`，langgraph 1.2.11 + Python 3.14.6）：
`interrupt()` **之前**的代码在 resume 后执行 **2 次**；中断前已完成的**其他**节点不重跑。

⇒ **写操作绝不能与 `interrupt()` 同处一个节点**（宪法一之附则一）。
`sales_order.confirm_token` 的 `UNIQUE` 约束是最后防线，**移除它等同于移除该附则**。

### 模型档位：五档的关闭 thinking 形状互不通用

`config/models.yaml` 的 M0–M4。**每档的 `extra_body` 形状不同，且是实测得来的，不可类推**：

| 档 | 关闭方式 | 状态 |
|---|---|---|
| M0 本机 Ollama | **关不掉**（`/v1` 端点），已决策保留 | 实测 |
| M1/M2 AutoDL 自建 | `chat_template_kwargs.enable_thinking` | ⚠️ **查文档所得，未实测**（阻塞于 D1） |
| M3 AutoDL.Art | 顶层 `enable_thinking` | 实测 ↓97% |
| M4 DeepSeek | `thinking.type=disabled` | 实测 ↓57% |

D1（AutoDL 实例）到位当天跑 `uv run python scripts/probe_vllm.py --tier M1` —— 它把半天调试
压成一次运行，且已用 M4 的已知答案做过对照验证。
**判据是 `completion_tokens` 而非「调用成功」** —— 实测有三种形状不报错但也不生效。

### 两个驱动共存

业务侧一律 **asyncpg**；**psycopg3 仅**用于 `langgraph-checkpoint-postgres`。
`tests/test_constitution_checks.py::test_psycopg3_is_not_mistakenly_banned` 守着这个例外。

## 工作方式

### 分支与合并

`main` 受保护：必过检查 `test`、**管理员不豁免**、禁止强推/删除、不要求 PR 审批。
直接 `git push origin main` 会被拒。流程见 `CONTRIBUTING.md`。

```bash
git checkout -b feat/p2-xxx && make check && git push -u origin feat/p2-xxx
gh pr create --fill && gh pr merge --squash --delete-branch
```

### 宪法第十一条：代码必须自带注释

注释写**为什么**，不写**是什么**。`i += 1  # i 加一` 是噪声。
CI 用 `ruff` 的 `pydocstyle`（`D`）把关；`# noqa: D` 抑制**必须同行给出理由**。

### 关键测试要做变异验证

本项目的惯例：写完关键断言后，**故意破坏实现，确认用例转红**。
一个不会失败的测试证明不了任何事 —— 这已抓出过多个「看起来通过实则失效」的检查。

### 出口判据是机械判定的

阶段完成由脚本判定，不由判断力判定。**判定脚本本身也会错**：
已修过「档位静默缩水仍报 PASS」「pytest 对 skip 返回 0 被当成通过」两处。

## 踩过的坑（省时间用）

| 现象 | 原因 |
|---|---|
| `docker exec psql -h 127.0.0.1` 能连但宿主连不上 | 容器内 loopback 命中 `pg_hba` 的 `trust`，**不校验口令**，是凭据验证的盲区 |
| grep `[0-9]+ skipped` 永不匹配 | `addopts` 已含 `-q`，命令行再传一个成 `-qq`，统计行被抑制。改按 `-rs` 的大写 `^SKIPPED` 匹配 |
| asyncpg 报 `timestamptz < interval` | interval 参数须 **`CAST(:x AS interval)` 与 `timedelta` 两者兼备**，缺一不可（psycopg 会自动转，asyncpg 不会） |
| CI 建库失败 `database "agentsystem" does not exist` | 脚本曾假设库已存在（开发机上是手工建的）。**本地残留状态掩盖问题**，正是要把 CI 推到干净环境跑的理由 |
| `gin_trgm_ops does not exist` | `pg_trgm` 须超级用户预建（`scripts/06_extensions.sql`），Alembic 以 `app_migrator` 运行建不了扩展 |
| PG 18 数据目录 | 挂 `/var/lib/postgresql`，**不是** `/var/lib/postgresql/data` |
| collation 版本不匹配卡死所有 `CREATE DATABASE` | 修复清单**必须含 `template1`**，漏它会复发且报错不指向它 |

## 已接受的风险（不要重复讨论）

- **Dify 以 `postgres`（超级用户）连库**（宪法附注 A.1）。超级用户绕过一切 ACL，
  故三层防篡改**防不住持有 Dify 数据库凭据的人**。切换脚本 `scripts/05` 已备好未执行。
  **在切换前，对外不得无限定地声称「审计日志不可篡改」。**
- 仓库为 **PUBLIC**（用户 2026-09-09 明确暂不转私有）。
- LangGraph 1.2.11 官方未声明支持 Python 3.14，实测可用且已固化为回归测试。
