# P1 详细设计

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 日期 | 2026-08-31 |
| 范围 | P1 地基阶段的**跨模块契约**。DDL 见[设计文档 §4](../superpowers/specs/2026-08-28-enterprise-ai-poc-design.md)，任务分解见 [WBS](../WBS.md) |
| 前提 | [项目宪法 11 条](../CONSTITUTION.md) |

---

## 0. 本文档设计什么、不设计什么

**判据：一个契约有几个下游依赖。** 只有多方依赖的才先设计——定错了要改动所有调用方。单模块内部的实现细节留到编码时决定，先写只会写成猜测。

| 设计 | 理由 |
|---|---|
| §2 `LLMGateway` 接口与 `models.yaml` | P2/P3/P4/P5 全部调用，改签名动全身 |
| **§2.5 身份与会话契约** | **三条红线全悬于此**：宪法一的操作者、宪法十的范围上界、`write_intent` 的会话绑定 |
| §3 审计装饰器与两段式时序 | 所有写操作挂它；评审指出的连接池自死锁与前值时机必须先定 |
| **§3.4 防篡改威胁模型** | **实测三层攻击**；属主可 `DISABLE TRIGGER` 拆掉整层，须加 Event Trigger |
| §4 `write_intent` 状态机 | 确认流程横跨 Gateway / LangGraph / 前端三方 |
| §5 错误模型 | RPA 降级判定（P4）靠它区分「业务拒绝」与「系统故障」 |
| §6 CI 机械检查规则 | 宪法 11 条与检查的映射关系，缺一条则该原则形同虚设 |
| §7 Makefile 目标契约 | 出口判据直接引用 |

**刻意不设计**：日志格式与字段（编码时按需定）· 单测的具体用例（TDD 时写）· 目录内文件的细粒度拆分（先跑起来再拆）· 前端（P3 才有）· 业务 API 的端点设计（P2）。

---

## 1. 目录结构与模块边界

```
agentsystem/
├── config/
│   └── models.yaml            # 五档模型配置，api_key 一律 ${ENV_VAR} 占位
├── src/agentsystem/
│   ├── gateway/               # FastAPI 入口层：鉴权、限流、审计埋点、分流
│   │   ├── app.py
│   │   ├── deps.py            # 会话身份、trace_id 生成（服务端权威）
│   │   └── errors.py          # §5 错误模型
│   ├── llm/                   # 模型抽象层
│   │   ├── gateway.py         # §2 LLMGateway
│   │   ├── config.py          # models.yaml 解析与校验
│   │   └── tiers.py           # 各档的 extra_body 差异（§2.3）
│   ├── audit/                 # 审计基座
│   │   ├── decorator.py       # §3 统一装饰器
│   │   ├── writer.py          # 两段式写入，独立小池
│   │   └── pool.py            # 审计专用连接池（max=3）
│   ├── intent/                # 写意图
│   │   └── store.py           # §4 write_intent 的铸造与原子消费
│   └── db/
│       ├── engine.py          # 业务池（asyncpg）
│       └── migrations/        # Alembic
├── scripts/                   # 运维与验证脚本
├── tests/
└── Makefile
```

**边界约定**：

- `llm/` **不得** import `audit/` 或 `db/`——模型层不知道审计存在，埋点由 `gateway/` 调用装饰器完成。反向依赖会让 `LLMGateway` 无法独立测试。
- `audit/` **不得** import `intent/`——装饰器校验令牌时经参数接收校验结果，不自己去查库。否则两个模块循环依赖。
- 模型端点 URL **只允许**出现在 `config/models.yaml` 与 `llm/config.py`（宪法第六条，CI 机械检查 §6）。

---

## 2. LLMGateway

### 2.1 `models.yaml` schema

```yaml
tiers:
  M0:
    name: "本机 Ollama"
    base_url: "http://127.0.0.1:11434/v1"
    model: "poc-chat"                    # Modelfile 固化 num_ctx=8192
    api_key: null                        # Ollama 不需要
    role: dev                            # dev | benchmark —— dev 档不进对比结论
    max_tokens_floor: 800                # ⚠️ 实测：thinking 关不掉，低于此值 content 为空
    extra_body: {}                       # 空是查证四种方式后确认无解，非配置遗漏
  M1:
    name: "AutoDL 5090 自建"
    base_url: "http://127.0.0.1:18001/v1"   # SSH 隧道
    model: "Qwen3.8-27B-NVFP4"
    api_key: "${AUTODL_SSH_PLACEHOLDER}"    # vLLM 若开鉴权
    role: benchmark
    max_tokens_floor: 512
    extra_body:
      chat_template_kwargs: { enable_thinking: false }
  M2:
    name: "AutoDL A100 自建"
    base_url: "http://127.0.0.1:18002/v1"
    model: "Qwen3.8-27B"
    role: benchmark
    extra_body:
      chat_template_kwargs: { enable_thinking: false }
  M3:
    name: "AutoDL.Art 托管"
    base_url: "https://www.autodl.art/api/v1"
    model: "Qwen3.5-397B-A17B"
    api_key: "${AUTODL_ART_API_KEY}"
    role: benchmark
    extra_body: { enable_thinking: false }      # ✅ 实测：1193 → 37 tok
  M4:
    name: "DeepSeek 官方"
    base_url: "https://api.deepseek.com"
    model: "deepseek-v4-flash"
    api_key: "${DEEPSEEK_API_KEY}"
    role: benchmark
    extra_body: { thinking: { type: disabled } } # ✅ 实测；顶层 enable_thinking 对 DeepSeek 无效
fallback_chain: [M1, M2, M3]     # 不可达时依次回落；M0 不参与（dev 档）
```

**加载时校验**（`llm/config.py`）：`api_key` 值若不以 `${` 开头且非 `null` 即拒绝启动——防明文密钥写进被 git 跟踪的文件（宪法第七条）。

### 2.2 接口契约

```python
class LLMGateway:
    """模型调用统一抽象层（宪法第六条的落地物）。

    职责：档位选择、extra_body 注入、超时重试、降级回落、用量计量。
    不负责：审计落库（由 gateway 层的装饰器完成）、业务语义。
    """

    async def chat(
        self,
        messages: list[Message],
        *,
        tier: str | None = None,        # None 则用 default_tier
        tools: list[ToolDef] | None = None,
        max_tokens: int | None = None,  # 低于该档 max_tokens_floor 时自动抬升
        temperature: float = 0.0,
        stream: bool = False,
    ) -> LLMResult: ...
```

```python
@dataclass(frozen=True)
class LLMResult:
    content: str | None
    tool_calls: list[ToolCall]          # 空列表表示无工具调用，不用 None
    finish_reason: str                  # stop | length | tool_calls
    tier_used: str                      # 实际使用的档，降级后与请求档不同
    degraded_from: str | None           # 非 None 表示发生过降级，供 UI 与审计打标
    # ── 以下四项喂给 audit_log，不在此处落库 ──
    ttft_ms: int | None                 # 流式才有
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
```

`tool_calls` 用空列表而非 `None`：调用方 `if result.tool_calls:` 即可，不必先判空。

### 2.3 档位差异的收敛点

四档的 thinking 开关形状互不通用，**差异全部收敛在 `models.yaml` 的 `extra_body` 段**，`gateway.py` 不含任何 `if tier == "M3"` 分支。新增档位只改配置。

| 档 | 关闭方式 | 状态 |
|---|---|---|
| M0 Ollama | 🔴 **`/v1` 端点无法关闭**（`think`/`PARAMETER`/`/no_think` 四法实测全败） | 保留 thinking |
| M1/M2 vLLM | `chat_template_kwargs.enable_thinking` | 待 D1 实测 |
| M3 AutoDL.Art | 顶层 `enable_thinking: false` | ✅ 实测 |
| M4 DeepSeek | `thinking.type = disabled` | ✅ 实测 |

### 2.4 降级链

```
请求 tier=M1 → 不可达 → M2 → 不可达 → M3 → 成功
                                          ↓
                    LLMResult.tier_used="M3", degraded_from="M1"
```

**触发条件**：连接失败 / 5xx / 超时。**不触发**：4xx（配置或请求错误，回落无意义）、`finish_reason="length"`（模型正常工作）。

降级须写入 `audit_log.model_tier`（记实际档）与响应元数据。**P1 无 UI，故只落元数据与审计**，UI 打标在 P3。

---

## 2.5 身份与会话契约

**三条红线全悬在这一节上**，而它此前全项目无定义——宪法第一条的「操作者」、第十条的过滤器上界、`write_intent` 的会话绑定，三者都从「会话」取值，却没有一份文档说会话怎么建立。若最终落成「前端传一个 `X-User-Id` 头」，越权用例失去可测对象、审计失去举证力、令牌绑定形同虚设。

### 2.5.1 PoC 阶段的认证方式

**静态 Bearer Token 映射到用户**，不做登录流程、不签发 JWT。PoC 只需要「身份是服务端权威的」这一性质，不需要完整的认证体系。

```yaml
# config/users.yaml —— 与 P2 §5.2 的权限配置同一份文件
users:
  - user_id: u_yr_01
    name: 印染销售
    token_sha256: "..."          # 存哈希，不存明文
    bu_codes: [BU-A]
    regions: [华东, 华南]
```

Token 由 `.env` 注入（宪法第七条），配置里只存 SHA-256。

### 2.5.2 RequestContext

```python
@dataclass(frozen=True)
class RequestContext:
    """请求级身份上下文。由 Gateway 中间件构造，全链路只读。

    frozen=True 是刻意的——任何下游代码都不得修改身份或权限范围。
    """
    user_id: str
    session_id: str          # 服务端生成，不接受客户端指定
    trace_id: str            # 同上，见 §2.5.3
    bu_codes: frozenset[str] # 权限上界，宪法第十条的 allowed 集合
    regions: frozenset[str]  # 同上；{"*"} 表示不限
```

**存放方式**：`contextvars.ContextVar[RequestContext]`，由中间件在请求入口 set。

**为什么用 contextvar 而不是函数参数**：db 层要在每次查询时强制注入范围过滤（P2 §5.1），若靠参数传递，每个端点、每个查询函数都要记得传，**漏一个就是越权**；contextvar 让 db 层能自己取，忘不掉。代价是隐式依赖，用 `frozen=True` 与「只在中间件 set」两条约束抵消。

### 2.5.3 session_id 与 trace_id 的生成

| | 生成方 | 是否接受客户端传入 | 理由 |
|---|---|---|---|
| `session_id` | 服务端 | ❌ | 客户端可指定即可冒充他人会话完成 `write_intent` 确认 |
| `trace_id` | 服务端（UUIDv7） | ❌ | 客户端可指定即可让两次操作共用一个 id，污染 attempt/outcome 串联 |

若入站带了 `X-Trace-Id`，**降级存入 `audit_log.client_trace_id` 作参考，不作为权威**。

UUIDv7 而非 v4：它自带时间前缀，审计表按 `trace_id` 排序即近似时序，省一个索引。

### 2.5.4 与三条红线的对接

| 红线 | 取值 |
|---|---|
| 宪法一 · 操作者 | `audit_log.user_id = ctx.user_id` |
| 宪法十 · 范围上界 | `effective = requested ∩ ctx.bu_codes` |
| `write_intent` 消费 | `WHERE session_id = ctx.session_id` |

**三处都从同一个 `ctx` 取，不允许任何一处另起炉灶。** CI 加一条检查：`audit_log` 的写入路径、`write_intent` 的消费路径、db 层的范围注入，三处的身份来源必须是 `ctx`，不得出现从请求体取 `user_id` 的代码。


---

## 3. 审计装饰器与两段式写入

### 3.1 为什么两段式

单事务写审计 → 业务失败回滚时**审计一并消失**，而"被拒绝的写尝试"恰恰是审计最关心的事件。分离事务 → 有"业务已提交、审计未落"的窗口。

两段式取中：**attempt 行独立提交在前，outcome 行独立提交在后**，同 `trace_id` 串联。崩溃留下的是**可检测的悬挂 attempt**，而非静默丢失。

### 3.2 时序

```
① 校验 confirm_token（intent.store 原子消费，返回 payload）
② audit.writer 用【独立池】写 attempt 行  ── 独立提交
        phase='attempt'  status=NULL  before_value=NULL
③ 业务事务开始（业务池）
     ├─ SELECT ... FOR UPDATE 目标行 → 读 before_value
     ├─ 执行写入
     └─ COMMIT / ROLLBACK
④ audit.writer 用【独立池】写 outcome 行 ── 独立提交
        phase='outcome'  status=success|failed|cancelled
        before_value / after_value 均在此行
```

**两条不可动的约束**：

1. **审计走独立小池（`max_size=3`），不得与业务共池。** 业务事务已持一条连接，再从同池 `acquire` 第二条——池大小 N 时 N 个并发全部卡死在 acquire。这是死锁而非变慢。
   **2026-09-02 实测**（池大小 5、并发 5，`pool_timeout=3s`）：
   共池 **0/5 成功**，全部 `TimeoutError`；独立池 **5/5 成功**，耗时 0.11s。
   没有 `pool_timeout` 兜底时即为永久挂起。
2. **`before_value` 只落 outcome 行。** 前值必须在业务事务内、对目标行加锁后读；attempt 行写在事务之外且之前，那时读到的值随时可能被他人改掉，不可作为举证。

### 3.3 装饰器签名

```python
def audited(
    action_type: Literal["read", "write"],
    *,
    target_table: str | None = None,
    require_confirm: bool = False,      # write 且需二次确认时置 True
):
    """统一审计装饰器（宪法第一条的落地物）。

    require_confirm=True 时强制校验 confirm_token —— 未确认直接调用抛
    ConfirmationRequired，这是红线的唯一机械保障，须有对应负测试。
    """
```

**约定**：`trace_id` / `session_id` / `user_id` 由 `gateway/deps.py` 注入 request context，装饰器从 context 取，**不从函数参数取**——否则每个业务函数都要多三个参数，且调用方可伪造。

---

## 3.4 审计防篡改的威胁模型（2026-08-31 实证）

评审曾指出「若 Alembic 用属主账号跑迁移，属主恒有全部权限且可 `DISABLE TRIGGER`，防篡改整层形同虚设」。该判断**部分成立**，实测结果如下。

### 3.4.1 三层威胁的实测

在临时库中按 P1.3.4 + P1.3.5 的设计建四角色（全部 `NOSUPERUSER`）、建表、施加 `REVOKE` 与行触发器，逐层攻击：

| 攻击者 | 动作 | REVOKE + 行触发器 | 加 Event Trigger 后 |
|---|---|---|---|
| `app_rw`（运行时） | INSERT | ✅ 允许 | ✅ |
| `app_rw` | UPDATE / DELETE / TRUNCATE | ✅ `permission denied` | ✅ |
| `app_rw` | DROP TABLE | ✅ `must be owner` | ✅ |
| **`app_migrator`（属主）** | UPDATE / TRUNCATE | ✅ 行触发器拦住 | ✅ |
| **`app_migrator`** | **`ALTER TABLE … DISABLE TRIGGER`** | 🔴 **成功，此后可任意篡改** | ✅ **被拦** |
| **`postgres`（超级用户）** | `DISABLE TRIGGER ALL` | 🔴 直接绕过 | ✅ **被拦** |

**两个超出预期的结论**：

1. **行触发器对属主有效**——属主直接 UPDATE/TRUNCATE 会被拦。评审假设「属主恒有全部权限」在这一层不成立，权限与触发器是两套机制。
2. **Event Trigger 对超级用户同样有效**——DDL 事件触发器在命令执行时触发，不区分调用者角色。

### 3.4.2 因此 P1.3.4 必须包含 Event Trigger

只有 `REVOKE` + 行触发器，属主一句 SQL 即可拆掉整层——**而 Alembic 正是以属主身份运行**，这条路径天天都在用。

```sql
CREATE OR REPLACE FUNCTION guard_audit_ddl() RETURNS event_trigger
LANGUAGE plpgsql AS $$
DECLARE r record;
BEGIN
  FOR r IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP
    IF r.object_identity LIKE '%audit_log%' AND r.command_tag = 'ALTER TABLE' THEN
      RAISE EXCEPTION '禁止对 audit_log 执行 ALTER（宪法第一条）：%', r.object_identity;
    END IF;
  END LOOP;
END $$;

CREATE EVENT TRIGGER trg_guard_audit_ddl
  ON ddl_command_end EXECUTE FUNCTION guard_audit_ddl();
```

⚠️ **副作用**：该事件触发器会拦住**所有**对 `audit_log` 的 `ALTER`，包括后续合法的 schema 变更。迁移需要改这张表时，流程是「超级用户 `DROP EVENT TRIGGER` → 迁移 → 重建」，且**该操作本身应被记录**。这是刻意的摩擦——审计表的结构变更就该是需要显式解锁的动作。

### 3.4.3 能力边界（须写入 PoC 报告）

**超级用户仍可 `DROP EVENT TRIGGER` 后绕过。** 数据库内部机制的上限就在这里——再往上需要数据库之外的手段：WORM 存储、审计日志实时外发到独立系统、或数据库审计插件（pgAudit）。

**PoC 阶段接受这个边界**，因为超级用户凭据不下发、且该绕过需要多步显式操作。**规模化落地前必须补外部手段**——这一条应与 §6.1 的「数据安全性维度无实测证据」并列写入报告的结论边界。


---

## 4. write_intent 状态机

```
                    ┌─────────────┐
   铸造（服务端）  →  │   pending   │
                    └──┬───┬───┬──┘
       用户确认 ────────┘   │   └──────── expires_at 到期
            ↓              │                    ↓
      ┌───────────┐   用户拒绝              ┌─────────┐
      │ confirmed │        ↓                │ expired │
      └───────────┘   ┌───────────┐         └─────────┘
                      │ cancelled │
                      └───────────┘
```

**四条转移规则**：

1. `pending → confirmed`：唯一路径是 §4.1 的原子 UPDATE。成功即执行，失败（0 行）说明令牌已被消费/过期/会话不符。
2. `pending → cancelled`：用户拒绝。**同样要写审计**（`status='cancelled'`）——很多设计漏掉"被拒绝的确认"。
3. `pending → expired`：惰性判定（消费时 `expires_at > now()` 不满足即视为过期），**不需要后台清理任务**。定期清理仅为控制表膨胀，非正确性所需。
4. **终态不可逆**。用户拒绝后若想改单，是**作废旧令牌 + 铸造新令牌**，不是把 cancelled 改回 pending。

### 4.1 原子消费

```sql
UPDATE write_intent
   SET state = 'confirmed', consumed_at = now()
 WHERE confirm_token = $1
   AND session_id    = $2        -- 身份取自 Gateway 会话，不取自请求体
   AND state         = 'pending'
   AND expires_at    > now()
RETURNING payload;
```

READ COMMITTED 下无双花：第二个 UPDATE 阻塞于行锁，第一个提交后重算谓词，`state` 已变则返回 0 行。

⚠️ **若业务事务用 REPEATABLE READ，此处抛序列化错误而非返回 0 行**——须捕获，或把该事务锁定为 READ COMMITTED。

### 4.2 三条不可省的约束

1. **令牌绝不进入模型上下文**。经 SSE 独立事件通道或 REST 交付前端。若由模型输出，注入可诱导它在后续轮次把令牌当工具参数发出——人类从未被询问。
2. **执行只取库中 `payload`，确认请求不得携带业务参数**。否则合法令牌配一份篡改载荷即可绕过：重放防住了，参数篡改没防。
3. **至多一次语义**：执行失败则令牌作废，不重试。重复提交凭 `result_ref` 返回原结果。

---

## 5. 错误模型

P4 的 RPA 降级判定靠它区分「业务拒绝」与「系统故障」——**若库存不足返回 5xx，降级逻辑会误触发 RPA 兜底**。

```python
class AppError(Exception):
    code: str            # 稳定的机器可读串，不随文案变
    message: str         # 面向用户的中文说明
    retryable: bool      # 决定是否重试 / 是否触发降级
    http_status: int
```

| 类 | code 前缀 | retryable | HTTP | 例 |
|---|---|---|---|---|
| `BusinessRejection` | `BIZ_` | **False** | 409 | 库存不足、跨缸超容差、订单已锁定 |
| `ValidationError` | `VAL_` | False | 400 | 槽位缺失、枚举非法 |
| `AuthError` | `AUTH_` | False | 401/403 | 越权、令牌无效 |
| `ConfirmationRequired` | `CONF_` | False | 428 | 写操作未确认（宪法第一条的负测试对象） |
| `UpstreamError` | `UP_` | **True** | 502/504 | LLM 不可达、Dify 超时 |
| `SystemError` | `SYS_` | True | 500 | 数据库连接失败 |

**统一响应包络**：

```json
{ "code": "BIZ_INSUFFICIENT_STOCK", "message": "B2409 缸可用量 800 米，不足订单所需 1200 米", "retryable": false, "trace_id": "..." }
```

`retryable` 是**降级判定的唯一依据**，调用方不得靠 HTTP 状态码猜。

---

## 6. CI 机械检查规则

宪法 11 条中，P1 应落地的每条对应一项可机械执行的检查。**没有检查的原则等于没有原则**。

| 宪法 | 检查 | 实现 |
|---|---|---|
| 一 · 写操作二次确认 | 负测试：未确认直接调用抛 `ConfirmationRequired` | pytest 用例 |
| 三 · 向量库写入权归 Dify | `app_ro` 对 `dify` 库 INSERT 必须失败 | pytest + PG fixture |
| 四 · 全链路 async | 请求路径无 `requests` / `psycopg2` / `time.sleep`<br>⚠️ **禁的是 `psycopg2`（同步旧版），不得误伤 `psycopg`（v3，checkpointer 依赖）** | ruff 自定义规则 / grep |
| 六 · 模型调用经 LLMGateway | 模型端点 URL 只出现在 `config/models.yaml` 与 `llm/config.py` | grep 白名单 |
| 七 · 机密只走环境变量 | ① `.env` 未被 git 跟踪 ② `models.yaml` 的 `api_key` 必须 `${` 开头 ③ pre-commit 挂 gitleaks | 三项 |
| 十一 · 代码必须自带注释 | `ruff` 的 `pydocstyle`（D）规则集<br>⚠️ 宪法要求「`# noqa: D` 须同行给理由」，**ruff 无此规则**，需一条 grep 补 | ruff + grep |
| — | **同一凭据的多份副本检测**（P0 教训，不对应宪法条款但保留） | 时间盒 0.3 人天，超时降级为 `grep '://.*:.*@'` 清单人工核对 |

**防篡改的 CI 断言**：`app_rw` 对 `audit_log` 的 `UPDATE` / `DELETE` / `TRUNCATE` 三者均须失败。

---

## 7. Makefile 目标契约

| 目标 | 行为 | 出口判据引用 |
|---|---|---|
| `make dev` | 起本地开发栈（Dify 已由其自身 compose 管理，此处只起我们的服务） | — |
| `make chat TIER=M0` | 单次对话，带 stub 工具，打印 `finish_reason` / `tool_calls` / usage | 判据 1 |
| `make backup` | 两条 `pg_dump -Fc` + `pg_dumpall --roles-only` + 打包 Dify storage | 判据 3 前置 |
| `make restore-drill` | 恢复到**干净实例**并比对两表行数 | 判据 3 |
| `make audit-tail` | 尾随最近 N 条审计，按 `trace_id` 分组显示 attempt/outcome 配对 | — |
| **`make p1-exit`** | **依次执行判据 1/2/3，输出 PASS/FAIL** | 出口判据 |

`make p1-exit` 是单人项目里唯一的机械裁判——没有它，"P1 完成了吗"就只能靠自我判断。

---

## 8. 实现顺序

依赖决定的强制顺序（与 WBS 的任务编号不同，WBS 按主题分组）：

```
P1.3.5 角色与授权          ← 必须最先，ALTER DEFAULT PRIVILEGES 只对此后建的对象生效
   ↓
P1.1.5 Alembic 框架        ← 用 app_migrator 跑
   ↓
P1.3.1 audit_log ─┬─ P1.4.1 write_intent
   ↓              ↓
P1.3.4 防篡改      P1.4.2 原子消费
   ↓              ↓
P1.3.2 两段式写入 ─┘
   ↓
P1.3.3 审计装饰器          ← 依赖 P1.4.2（校验令牌）
   ↓
P1.2.* LLMGateway          ← P1.2.4 埋点依赖 audit_log
   ↓
P1.2.5 make chat + stub 工具
   ↓
P1.5.* CI 与备份 ─→ P1.5.4 恢复演练（须在 make chat 之后，否则恢复空 schema 无意义）
   ↓
make p1-exit
```
