# P2 详细设计

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 日期 | 2026-08-31 |
| 范围 | P2 的**跨阶段契约**。表 DDL 见[设计文档 §4](../superpowers/specs/2026-08-28-enterprise-ai-poc-design.md)，任务分解见 [WBS](../WBS.md)，P1 契约见 [P1 详细设计](P1-DETAILED-DESIGN.md) |
| 前提 | [项目宪法 11 条](../CONSTITUTION.md) |

---

## 0. 本文档设计什么

沿用 P1 的判据：**只设计有多个下游依赖的契约**。P2 的下游密度比 P1 更高——它产出的工具 schema 同时被 P3 的 Agent、P4 的 Dify 工具导入、P5 的评测三方消费。

| 设计 | 下游 |
|---|---|
| §1 编码规则 | P3 的 L1 正则路由 · P2.2 数据生成 · P5 测试用例 |
| §2 **工具 schema 设计规则** | P3 Agent · P4 Dify 导入 · P5 评测 —— **本文档杠杆最大的一节** |
| §3 五个只读工具签名 + §3.2 写端点契约 | 同上；写端点是全项目唯一写路径 |
| §4 响应包络与分页语义 | 所有端点 · LLM 对结果的理解 |
| §5 权限注入点 | P3 检索过滤 · P4 越权用例 |
| §6 fixture 规格的结构 | P5 全部 40 条用例 |

**刻意不设计**：具体的模拟数据值（生成器的事）· 端点内部实现 · 数据库索引调优（几百行数据用不上）· 8 份知识文档的正文（P3）。

---

## 1. 编码规则

P3 的 L1 正则路由要靠它零延迟识别意图，所以编码必须**自带类型信息且互不歧义**。

| 实体 | 格式 | 例 | 正则 |
|---|---|---|---|
| 订单号 | `SO-{年}-{6位序号}` | `SO-2026-000123` | `SO-\d{4}-\d{6}` |
| 产品 | `P-{BU字母}-{4位}` | `P-B-2001` | `P-[ABC]-\d{4}` |
| 色号 | `C-{BU字母}-{4位}` | `C-A-0088` | `C-[ABC]-\d{4}` |
| 仓库 | `WH-{2位}` | `WH-03` | `WH-\d{2}` |
| 染缸批次 | `D{年月}-{2位}` | `D2601-08` | `D\d{4}-\d{2}` |
| 窑批次 | `K{年月}-{2位}` | `K2603-15` | `K\d{4}-\d{2}` |
| 注浆批次 | `J{年月}-{2位}` | `J2602-04` | `J\d{4}-\d{2}` |
| 产线 | `{类型字母}-{2位}` | `D-01` 染缸 · `K-03` 窑炉 · `F-02` 成型线 | `[DKF]-\d{2}` |
| 排产单 | `PP-{年月}-{4位}` | `PP-2603-0042` | `PP-\d{4}-\d{4}` |
| 政策 | `POL-{年}-{BU}-{2位}` | `POL-2026-B-01` | `POL-\d{4}-[ABC]-\d{2}` |

**两条约束**：

1. **BU 字母嵌进编码**（`P-B-2001` 一眼看出是建陶）。这让 L1 路由能从编码直接推断 BU，是 §5 权限校验的旁证来源——但**不是权限依据**，权限只认服务端注入的身份。
2. **三类批次前缀互斥**（`D` / `K` / `J`），因为三个 BU 的批次概念不同（染缸 / 窑批 / 注浆批），共用前缀会让「B2409 缸」这类表述无法定位到 BU。

> `policy_code` 必须能与 `sales_order.policy_code` 对上——这是「静态知识 × 动态数据融合问答」的连接键（brief 四项能力之一，此前在架构里无落点）。

---

## 2. 工具 schema 设计规则

**这是全项目对 function calling 准确率杠杆最大的一处。** 评审原话：「`grade` 声明成带 description 的 enum 还是自由字符串，对 ≥90% 端到端成功率的影响远大于大多数 prompt 调整。」

### 2.1 🔴 硬红线：三类参数绝对不暴露给模型

宪法第十条要求「数据范围过滤器由服务端依据用户身份注入，不接受模型或前端指定」。落到工具 schema 上就是：

| 参数 | 可否出现在 schema | 语义 |
|---|---|---|
| `user_id` / `session_id` / `tenant` | 🔴 **绝对禁止** | 身份不能由模型声明 |
| `bu_code` / `region` | 🟡 **可以，但只用于收窄** | 见下 |

**范围参数不是简单的「禁止」，而是「上界由服务端定，模型只能在内收窄」**（宪法第十条）。若完全禁止，S1 的「年度 × 区域 × 产品三维过滤、多轮追问细化」和跨 BU 用户的场景就实现不出来——集团质量部无法说「先看华东的」。

```
allowed = 服务端从身份取出的集合        # 模型看不到
requested = 模型传入的范围参数（可为空）   # 空则用 allowed 全集
effective = requested ∩ allowed
若 effective 为空且 requested 非空 → 抛 AUTH_SCOPE_EXCEEDED（不静默降级）
```

**越界必须显式报错而非静默忽略**——静默会让越权尝试无法被审计发现，而「越权查询」是 §14 的验收用例。

范围参数的 description 须写明约束：

```python
region: str | None = Field(
    description="区域，如「华东」。仅用于在你已有权限范围内收窄查询，"
                "不能用于访问其他区域——越界会被服务端拒绝。留空则返回你有权查看的全部区域。"
)
```

> 实现时最容易违反的地方是「顺手加个 `bu_code` 参数让模型指定，省得服务端判断」。写 schema 时若发现某个参数能让模型跨越数据边界，它就该从 schema 里删掉。

### 2.2 命名与描述的写法

**工具名**：`{动词}_{名词}`，全小写下划线，动词限定在 `query` / `create` / `update` 三个。模型靠动词判断读写，`get_inventory` 与 `check_inventory` 混用会让它在写场景下也选到读工具。

**工具 description**：必须回答「什么时候该选它」，而不是「它做什么」。

```
❌ "查询库存信息"
✅ "按产品、仓库、批次维度查询实时库存。用户询问某产品还有多少货、
    哪个仓库有货、某批次的等级与色差时使用。不用于查询在产数量——
    那属于排产查询。"
```

末句的**负向说明**（"不用于……"）比正向描述更能减少误选，因为五个工具的正向描述天然相似。

**参数 description**：写**取值示例与约束**，不复述参数名。

```
❌ region: "区域"
✅ product_code: "产品编码，格式 P-{BU字母}-{4位数字}，如 P-B-2001。
                  若用户只说了产品名（如「岩板」），先调 query_product 取编码。"
```

最后一句把**工具间的调用顺序**写进了 description——这是让模型正确串联多步查询的最省事办法。

### 2.3 枚举一律用 `Literal`，且每个值带说明

```python
Grade = Literal["优等品", "一等品", "合格品"]
BatchPolicy = Literal["SAME_BATCH", "CROSS_OK_WITHIN_TOL", "ANY"]
OrderStatus = Literal["待确认", "已确认", "生产中", "已发货", "已完成", "已取消"]
```

在 `Field(description=...)` 中逐值解释：

```python
batch_policy: BatchPolicy = Field(
    description=(
        "批次策略。SAME_BATCH=必须同一染缸/窑批供货，库存不足时不得拼单；"
        "CROSS_OK_WITHIN_TOL=可跨批但两批间 ΔE 差值须 ≤ delta_e_tolerance；"
        "ANY=不限，各批单独满足等级即可。用户未明确时须追问，不得默认。"
    )
)
```

末句「用户未明确时须追问，不得默认」把**澄清义务写进了 schema**，比在 system prompt 里统一交代更可靠——它离参数最近。

### 2.4 可选参数的 3.1 schema 兼容性

FastAPI 对 `str | None` 生成 OpenAPI 3.1 的 `anyOf: [{type:string},{type:null}]`，而部分工具导入器只认 3.0 风格的 `nullable`。**P2.3.6 的探针第一天就要试**，别等 P4 发现十二项 Dify 场景的工具层要返工。

若 Dify 导入失败，退路是为 Dify 单独导出一份 3.0 schema（FastAPI 支持指定 `openapi_version`），而非改动业务模型。

---

## 3. 五个工具的签名

对应五个主线场景的**只读**部分。写端点见 §3.2——它不进工具集（宪法第二条：写路径只走 LangGraph，且 Dify 导入的工具集里不得有写端点）。

```python
query_policy(
    year: int,                    # 年度，如 2026。不传则默认当前年度
    product_line: str | None,     # 产品线，如 "岩板"。为空则返回该年度全部政策
    policy_code: str | None,      # 政策编码 POL-2026-B-01。已知编码时优先用它，精确匹配
    limit: int = 20, offset: int = 0,
) -> Paged[PolicyBrief]

query_product(
    keyword: str,                 # 产品名关键词，如 "岩板" "白坯布"。自然语言→编码的入口
    category: str | None,
    limit: int = 20, offset: int = 0,
) -> Paged[Product]

query_inventory(
    product_code: str | None,     # 未知编码时先调 query_product
    color_code: str | None,
    warehouse_code: str | None,
    batch_no: str | None,         # 染缸 D2601-08 / 窑批 K2603-15 / 注浆批 J2602-04
    grade: Grade | None,
    limit: int = 20, offset: int = 0,
) -> Paged[InventoryBatch]

query_order(
    order_no: str | None,         # SO-2026-000123，已知时优先，精确匹配
    customer_code: str | None,
    status: OrderStatus | None,
    date_from: date | None,       # ISO 8601
    date_to: date | None,
    limit: int = 20, offset: int = 0,
) -> Paged[OrderBrief]

query_production_plan(
    order_no: str | None,         # 传入则返回该订单关联的全部排产及交期推演
    line_code: str | None,
    date_from: date | None,
    date_to: date | None,
    limit: int = 20, offset: int = 0,
) -> Paged[ProductionPlan]
```

**注意 `query_product` 的存在本身就是设计结论**：没有它，「白色岩板的库存」无法落到 `product_code`——此前产品名只在 RAG 产品手册里，等于要求模型先检索再查 SQL，把 RAG 误差引进确定性查询路径。

### 3.2 创建订单写端点

§3 的五个工具**均为只读**且会被导入 Dify；写端点**不进工具集**（宪法第二条：写路径只走 LangGraph），由 LangGraph 的下单子图直接调用。

```python
POST /api/v1/orders

class CreateOrderRequest(BaseModel):
    confirm_token: str                 # 必填。无令牌直接抛 ConfirmationRequired（宪法第一条）
    # ⚠️ 不含任何业务字段 —— 载荷从 write_intent.payload 取
```

**请求体只有令牌，没有业务参数**，这是宪法第十条的直接要求：合法令牌配一份篡改过的载荷即可绕过二次确认，**重放防住了、参数篡改没防**。业务载荷在用户确认时已存入 `write_intent.payload`，执行时只从库里取。

**幂等**：`sales_order.confirm_token` 有 UNIQUE 约束（P2.1.3）。

```
INSERT ... ON CONFLICT (confirm_token) DO NOTHING RETURNING order_no
  ├─ 有返回 → 首次创建，返回 201
  └─ 无返回 → 重复提交，SELECT 既有行返回 200 + 原 order_no
```

用唯一索引仲裁竞态，不用应用层「先查后写」——后者在并发下必然有窗口。

**执行时序**（与 [P1 详细设计 §3.2](P1-DETAILED-DESIGN.md#32-时序) 的两段式对齐）：

```
① 原子消费令牌（P1 §4.1），拿到 payload；0 行则抛 CONF_TOKEN_INVALID
② 写 attempt 审计行（独立池）
③ 业务事务：
     ├─ SELECT ... FOR UPDATE 候选批次 → 复检库存
     │    不足则 ROLLBACK，抛 BIZ_INSUFFICIENT_STOCK（retryable=false，不触发 RPA 降级）
     ├─ INSERT sales_order ... ON CONFLICT DO NOTHING
     ├─ INSERT sales_order_line（line_no 从 1 递增）
     └─ COMMIT
④ 写 outcome 审计行，含 before_value / after_value
```

**③ 的库存复检不可省**：确认卡片生成到用户点确认之间隔着人的思考时间，库存可能已被他人占用。**本期不做库存预占**（要配套释放与超时回收，成本不小），代价是复检失败时退回追问——这比预占更诚实。

**响应**：

```json
{ "data": { "order_no": "SO-2026-000123", "status": "已确认",
            "lines": [ ... ], "created_at": "..." },
  "trace_id": "..." }
```

### 3.1 S5 的延误推演

`query_production_plan` 传 `order_no` 时，除排产记录外额外返回：

```python
class DelayAssessment(BaseModel):
    order_no: str
    required_date: date
    estimated_completion: datetime    # 末道工序 plan_end（按 stage_seq 排序取最后）
    will_delay: bool
    delay_days: int                   # 负数表示提前
    bottleneck_stage: str | None      # 造成延误的工序
    reasoning: str                    # 人类可读的推演说明，供 LLM 直接引用
```

`reasoning` 是**服务端算好的自然语言**，不是让模型自己推。延误判定涉及工序顺序、产能、换型损耗，交给模型算必然出错；服务端算完给结论，模型只负责组织表达。

---

## 4. 响应包络与分页语义

### 4.1 统一包络

沿用 [P1 详细设计 §5](P1-DETAILED-DESIGN.md#5-错误模型) 的错误模型。成功响应：

```json
{
  "data": [ ... ],
  "pagination": { "limit": 20, "offset": 0, "total_count": 137, "has_more": true },
  "trace_id": "..."
}
```

### 4.2 🔴 截断必须让模型知道

**无分页会让 LLM 把截断结果当成完整结果——表现为幻觉，成因却是接口设计。** 评审原话。

仅返回 `has_more: true` 不够，模型未必注意到。**当 `has_more` 为真时，在 `data` 之外附一条自然语言提示**：

```json
{
  "data": [ /* 20 条 */ ],
  "pagination": { "total_count": 137, "has_more": true },
  "notice": "共 137 条，此处仅返回前 20 条。如需完整结果请缩小查询范围（指定仓库或批次），或说明需要查看更多。"
}
```

`notice` 字段进入模型上下文，比结构化的布尔值有效得多。

### 4.3 空结果与业务拒绝的区分

| 情形 | 返回 | 为什么不能混 |
|---|---|---|
| 查询无匹配 | `200` + `data: []` + `notice: "未找到符合条件的记录"` | 这是正常结果，不是错误 |
| 库存不足（写操作时） | `409` + `code: "BIZ_INSUFFICIENT_STOCK"` + `retryable: false` | 这是业务拒绝，**不得触发 P4 的 RPA 降级** |
| 上游超时 | `504` + `code: "UP_TIMEOUT"` + `retryable: true` | 这才该触发降级 |

---

## 5. 权限注入点

### 5.1 在哪一层注入

```
请求 → gateway/deps.py 解析会话 → RequestContext{user_id, bu_codes, regions}
                                        ↓
                          端点函数签名中**不含**这三项
                                        ↓
                    db 层从 contextvar 取出，强制拼进 WHERE
```

**注入发生在 db 层而非端点层**——若在端点层拼，每个端点都要记得拼，漏一个就是越权。放在 db 层的查询构造器里，**忘不掉**。

### 5.2 静态权限配置

```yaml
users:
  - user_id: u_yr_01
    name: 印染销售
    bu_codes: [BU-A]
    regions: [华东, 华南]
  - user_id: u_qc_01
    name: 集团质量部
    bu_codes: [BU-A, BU-B, BU-C]    # 跨 BU 用户，触发 P3 的会话粘性追问
    regions: ["*"]
```

PoC 用配置文件而非 RBAC 表。**但「越权查询」是 §14 的验收用例，没有这个最小模型该指标就没有可测对象**——不是可选项。

### 5.3 与 RAG 检索的一致性

同一份 `bu_codes` 同时用于：① SQL 的 WHERE ② Dify 检索的 `metadata_filtering_conditions`。**两处必须取自同一 context**，否则会出现「SQL 查不到但知识库能查到」的不一致越权。

---

## 6. fixture 规格的结构

P2.2.1 的产出物不是数据，是**一张把验收用例映射到数据条件的表**。P5 的 40 条用例全部依赖它。

| 用例 | 依赖的数据形态 | 断言 |
|---|---|---|
| S2-03 跨缸澄清 | 存在 (product, color) 组合拥有 **≥3 个批次**，且批次间 ΔE 差值 >1.0 | `SELECT ... HAVING count(*)>=3` |
| S3-05 库存不足 | 存在订单行需求量 > 该 (product,color) 全部可用量之和 | 断言存在 |
| S5-02 真延误 | 存在订单其末道工序 `plan_end` > `required_date` | 断言存在 |
| 边界-04 跨 BU 同名 | 存在两个不同 BU 的产品 `product_name` 高度相似 | 断言存在 |
| 边界-07 待检批次 | 存在 `qc_status='待检'` 的批次 | 断言存在 |

**生成器必须固定随机种子**，且**生成后跑断言校验**——P0-2 造数据时 `bu` 与 `year` 都用 `i%2`，两维完全相关，四维过滤查询直接返回 0 行。**均匀随机生成产不出关键形态**，这一条已有现场教训。

### 6.1 产品目录规模的约束

算一遍：100 条库存批次，若要每个 (product, color) 平均有 3 个批次（跨缸澄清的前提），只能支撑约 33 个组合，再乘等级维度 → **每 BU 约 10–15 个 SKU、6–8 个色号**。

**少品种、多批次** 是硬约束。按 SKU 铺开会让绝大多数组合只有 1 个批次，招牌的跨缸澄清演示直接没数据可演。

---

## 7. 实现顺序

```
P2.4.3 预注册评分细则     ← 必须最先，且在实现之前 commit 冻结
   ↓                        （看到结果再定标准，标准会向结果对齐）
P2.1.1/2 主数据 ─→ P2.1.4/6/7 库存 ─→ P2.1.3 订单 ─→ P2.1.5 排产
   ↓                                                      ↓
P2.4.2 补 region 字段 ──────────────────────────────→ P2.1.8 Alembic
                                                          ↓
P2.2.1 fixture 规格 → P2.2.2 生成器 → P2.2.3 断言校验
                                                          ↓
P2.3.1 FastAPI 骨架 → P2.3.2 包络 → P2.3.3 分页 → P2.3.4 端点
                                                          ↓
P2.4.1 权限注入（db 层）→ P2.3.5 OpenAPI 导出 → P2.3.6 Dify 导入探针
                                                          ↓
                                                    P2.4.4 API 文档
```

**P2.4.3 排在最前**不是形式主义：评分细则若在看到实现结果之后才定，标准会无意识地向观测结果对齐——这是 §14 已确立的原则（预注册），P0-6 也是这么做的。

**P2.3.6 的 Dify 导入探针**虽然编号在后，但只要 P2.3.5 一出 schema 就该立刻试，不要攒到阶段末——它若失败，影响的是 P4 十二项 Dify 场景的工具层。
