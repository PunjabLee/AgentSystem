# 部署运维说明

> 起稿于 P1（环境搭完即写，不留到 P5）。P0/P1 踩到的坑逐条记录在
> [§6 故障排查](#6-故障排查)，那一节的价值高于其余全部内容 ——
> 每一条都是花了小时级时间才定位的。

---

## 1. 组件与端口

| 组件 | 形态 | 端口 | 归谁管 |
|---|---|---|---|
| PostgreSQL 18.6 + pgvector 0.8.6 | Dify compose 内的 `db` 容器 | 5432（对宿主暴露） | **Dify compose** |
| Dify 1.17.0 | 14 个容器 | 80 / 443 | Dify compose |
| Ollama | 宿主原生进程 | 11434 | 手工 / launchd |
| 本系统 | 宿主 Python 进程 | 待 P2.3.1 | 本仓库 |

**PostgreSQL 归 Dify compose 管，不单独起一个。** 两个 PG 实例意味着两套备份、
两套角色、两套版本，而 PoC 阶段没有任何理由付这个代价。本系统的
`agentsystem` 库与 Dify 的 `dify` 库同集群共存，靠角色隔离
（见 [§3 数据库角色](#3-数据库角色)）。

---

## 2. 首次部署

```bash
# ① Dify 栈（含 PostgreSQL）
cd <dify>/docker && docker compose up -d

# ② 数据库角色与授权（超级用户）
#    ⚠️ 必须在 ① 之后 —— 本步骤要对 dify 库执行 REVOKE，缺库会**直接失败**
#       （刻意的，见下方说明）。
cd <本仓库>
psql -h 127.0.0.1 -U postgres -v ON_ERROR_STOP=1 \
     -v migrator_pw="$PG_MIGRATOR_PASSWORD" \
     -v app_pw="$PG_APP_PASSWORD" \
     -v ro_pw="$PG_RO_PASSWORD" \
     -f scripts/01_roles_and_grants.sql
psql -h 127.0.0.1 -U app_migrator -d agentsystem -f scripts/02_grants_in_agentsystem.sql

# ③ 第四角色与库级隔离（超级用户）
psql -h 127.0.0.1 -U postgres -v ON_ERROR_STOP=1 \
     -v dify_owner_pw="$PG_DIFY_OWNER_PASSWORD" \
     -f scripts/04_database_isolation.sql

# ④ 建表
make migrate

# ⑤ 审计防篡改的第三层（必须超级用户，故不在迁移里）
psql -h 127.0.0.1 -U postgres -d agentsystem -f scripts/03_audit_event_trigger.sql
```

### ⚠️ 步骤 ① 与 ② 的先后是安全控制的承重结构

`scripts/01` 里的 `REVOKE ALL ON DATABASE dify FROM PUBLIC` 是宪法第三条在授权层
的落地：应用侧读向量只走 Dify 的检索 API，`app_rw` 连 dify 的权限都不给，
「绕过 Dify 直写向量库」因而在授权层不可能发生。

**该控制只在 dify 库已存在时才能施加。** 所以脚本在缺库时**默认失败而非跳过** ——
曾经写成静默跳过，被安全评审判为 control-regression：真实部署里 Dify 的
compose 若后启动，这条 REVOKE 就永远不会执行，而 PostgreSQL 给新库默认授予
PUBLIC CONNECT，控制当场失效且无人察觉。

确无 Dify 的环境（如 CI）须**显式**放行：

```bash
psql ... -v allow_missing_dify=1 -f scripts/01_roles_and_grants.sql
```

放行时脚本会打印醒目告警。**Dify 部署完成后必须重跑 `scripts/01`**，否则
`app_rw` 可直连向量库。跳过安全控制必须是写出来的决定，不能是默认行为。

# ⑤ 本地模型与演示令牌
make ollama-models
make mint-tokens

# ⑥ 验收
make p1-exit
```

---

## 3. 数据库角色

| 角色 | 权限 | 谁在用 |
|---|---|---|
| `app_migrator` | `agentsystem` 库属主，可建表 | Alembic |
| `app_rw` | 业务表增删改查；**`audit_log` 上只有 INSERT/SELECT** | 运行时 |
| `app_ro` | 只读，含 `dify` 库的 CONNECT | 评测与排查 |
| `dify_owner` | `dify` / `dify_plugin` 的属主；**对 `agentsystem` 无 CONNECT** | Dify |

四者均 `NOSUPERUSER`。分离不是洁癖：迁移账号是表属主，若与运行时账号合一，
属主一句 `ALTER TABLE ... DISABLE TRIGGER` 就能拆掉审计防篡改的前两层。

`agentsystem` 已 `REVOKE ALL ... FROM PUBLIC` 并逐个显式授予 CONNECT
（`scripts/04_database_isolation.sql`）。这一步堵的是绕过审计防护最省事的
路径：不必拆触发器，换个能连库的角色登录即可。

### ⚠️ 已接受的风险：Dify 仍以 postgres（超级用户）连库

**超级用户绕过一切 ACL。** 只要 Dify 用 `postgres`，任何能拿到它数据库凭据
的路径都能对 `agentsystem` 执行 `DROP EVENT TRIGGER` 后随意改写 `audit_log`
—— P1.3.4 的三层防护对它完全无效，`scripts/04` 只关掉了非超级用户那条路径。

> **2026-09-03 决策：PoC 阶段接受现状，不切换。** 理由与边界见
> [宪法 附注 A.1](CONSTITUTION.md)。**规模化落地前必须执行下述切换** ——
> 在那之前，任何声称「审计日志不可篡改」的对外表述都须带上这条限定。

切换步骤（待执行）：

```bash
# ① 停 Dify
cd <dify>/docker && docker compose stop

# ② 转移两个库的对象属主（145 表 + 13 表，逐类显式 ALTER）
psql -U postgres -d dify        -f scripts/05_switch_dify_to_dify_owner.sql
psql -U postgres -d dify_plugin -f scripts/05_switch_dify_to_dify_owner.sql

# ③ 改 Dify 的 .env
#    DB_USERNAME=dify_owner
#    DB_PASSWORD=<PG_DIFY_OWNER_PASSWORD>

# ④ 起 Dify 并验证知识库检索正常
docker compose up -d
```

脚本刻意**不用** `REASSIGN OWNED BY postgres TO dify_owner`：该命令除当前库
的对象外还会改共享对象属主，包括 `postgres` / `template0` / `template1`
等数据库，一条命令波及整个集群且无法局部回滚。

### 连接数核算（P1.1.6）

现状 `max_connections = 200`，`superuser_reserved_connections = 3`。P1 的实际占用：

| 来源 | 连接数 |
|---|---|
| 业务池（asyncpg，`_APP_POOL_SIZE`） | 5 |
| 审计池（asyncpg，独立，`_AUDIT_POOL_SIZE`） | 3 |
| LangGraph checkpointer（psycopg3 池） | 待 P3 接入 |
| Dify 全栈实测 | 约 5–14 |

两个池均设 `max_overflow=0`：宁可排队暴露容量问题，也不悄悄突破核算。
**200 对 P1 绰绰有余，本阶段不调整。** 数据评审给的 P3 依据（峰值约 55、
建议 `max_connections=120` + `work_mem=8MB`）留待 P3 接入 checkpointer
与评测脚本后再核。

> ⚠️ **项目有两个 PG 驱动**：业务侧 asyncpg，`langgraph-checkpoint-postgres`
> 依赖 psycopg v3。宪法第四条禁的是 **`psycopg2`（同步旧版）**，
> `tests/test_constitution_checks.py` 里有一条反向断言专门守着这个区分 ——
> 误伤 psycopg3 会把 checkpointer 打挂，且要到运行时才发现。

---

## 4. 日常运维

| 操作 | 命令 |
|---|---|
| 备份（两库 + 角色 + Dify storage） | `make backup` |
| 恢复演练 | `make restore-drill` |
| 查审计（attempt/outcome 配对） | `make audit-tail N=50` |
| 模型冒烟 | `make chat` / `make chat TIER=M4` |
| 普通迁移 | `make migrate` |
| **改 `audit_log` 结构** | `make migrate-audit` ← 不能用 `make migrate` |

### 为什么 audit_log 的迁移要走单独命令

事件触发器 `trg_guard_audit_ddl` 会拦下**一切**对 `audit_log` 的 `ALTER`，
包括合法迁移。这是刻意的：审计表的结构变更就该是需要显式解锁的动作。
`scripts/migrate_audit_schema.sh` 做的是「解锁 → 迁移 → 重新上锁」，
并用 `trap` 保证无论成功、失败还是 Ctrl-C 都会重新上锁，**且复查
`pg_event_trigger` 确认恢复成功** —— 这是全流程唯一不能失败的一步，
只看退出码不够。

### 恢复的两个必做步骤

`pg_restore` 之后**必须**补两件事，否则新实例是残的：

0. **扩展由超级用户预建**：`psql -U postgres -d agentsystem -f scripts/06_extensions.sql`
   —— `pg_trgm`（product 名称模糊匹配）与 `vector`（Dify 向量存储）。
   `CREATE EXTENSION` 要求超级用户，Alembic 以 `app_migrator` 运行建不了。
   缺 `pg_trgm` 时迁移报 `operator class "gin_trgm_ops" does not exist`。

1. **`CREATE EXTENSION vector` 由超级用户预建**。带 `--role=app_migrator`
   恢复时这一句报 `Must be superuser`，转储里的扩展静默丢失。
2. **重建事件触发器**。它在转储里，但建它要超级用户，故必然恢复失败，
   而失败信息淹在 `pg_restore` 的输出里。

`scripts/restore_drill.sh` 两件都做了，并在最后实测三层防护是否复现 ——
「恢复成功」必须意味着新实例与原实例保护等同。

---

## 5. 机密

全部走环境变量（宪法第七条）。被 git 跟踪的配置里**只有占位与哈希**：

| 文件 | 承载什么 | 保障 |
|---|---|---|
| `.env` | 全部明文 | `.gitignore`，且有 pytest 断言它未被跟踪 |
| `config/models.yaml` | `${VAR}` 占位 | 加载时拒绝明文，启动即失败 |
| `config/users.yaml` | 令牌的 SHA-256 | pytest 断言是 64 位十六进制且不等于任何明文 |

只盯 `.env` 是不够的 —— 拦不住写进被跟踪文件的密钥，这是安全评审的阻塞项。

---

## 6. 故障排查

### 6.1 PG 18 的挂载路径变了

**症状**：换用 PG 18 镜像后容器起不来，或数据目录看似为空。

**原因**：PG 18 的官方镜像把数据目录约定从 `/var/lib/postgresql/data`
改为 **`/var/lib/postgresql`**。沿用旧约定会挂错层。

```yaml
volumes:
  - ./volumes/db/data:/var/lib/postgresql   # ✅ PG 18
  # - ./volumes/db/data:/var/lib/postgresql/data   # ❌ PG 17 及更早
```

### 6.2 collation 版本不匹配 —— 修复清单必须含 template1

**症状**：`WARNING: database "xxx" has a collation version mismatch`，
严重时**全部 `CREATE DATABASE` 卡死**。

**原因**：跨 Debian 版本换镜像后 glibc 的 collation 版本变了。

**修复**（P0 阶段此坑出现过三次，前两次都因为漏了 `template1` 而复发）：

```sql
REINDEX DATABASE <每一个库>;
ALTER DATABASE <每一个库> REFRESH COLLATION VERSION;
-- 🔴 template1 必须一起修。漏它 → 新建库继承坏掉的 collation 版本 →
--    CREATE DATABASE 卡死，且报错信息不指向 template1。
ALTER DATABASE template1 REFRESH COLLATION VERSION;
ALTER DATABASE template0 REFRESH COLLATION VERSION;  -- 需先允许连接
```

清单写法：`SELECT datname FROM pg_database;` 出来的**每一个**都要过一遍，
不是只过业务库。

### 6.3 容器访问宿主服务

**症状**：Dify 容器里配 `http://127.0.0.1:11434` 连不上 Ollama。

**原因**：容器里的 `127.0.0.1` 是容器自己。

**修复**：用 `host.docker.internal`（Docker Desktop for Mac/Windows 内置）。
Linux 上需在 compose 里显式加：

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

### 6.4 同一凭据的多份副本

**症状**：改了 Redis 口令后 Celery worker 失联，且**改动看起来已经生效**。

**原因**：`CELERY_BROKER_URL` 里内嵌了第二份口令，与 `REDIS_PASSWORD` 分叉。
改一处不改另一处，两者静默不一致 —— P0 阶段因此丢了 24 小时。

**修复**：让派生变量插值而非复制。

```bash
REDIS_PASSWORD=<口令>
CELERY_BROKER_URL=redis://:${REDIS_PASSWORD}@redis:6379/1   # ✅ 插值
# CELERY_BROKER_URL=redis://:<又写一遍口令>@redis:6379/1     # ❌ 副本
```

**排查手法**：`grep -E '://.*:.*@' .env docker-compose.yaml` 列出全部内嵌
凭据的 URL，逐条核对是否与权威变量一致。

### 6.5 Ollama 常驻内存过高

**症状**：`qwen3:8b` 与 embedding 模型无法共存，必须错峰使用。

**原因**：默认上下文长度让两者分别常驻 11 GB / 5.8 GB。

**修复**：用 Modelfile 固化 `num_ctx`（本仓库 `ollama/*.Modelfile`），
降至 6.6 GB / 2.1 GB，错峰约束取消。`make ollama-models` 重建。

### 6.6 M0 的 thinking 关不掉

**症状**：M0 走 OpenAI 兼容端点 `/v1` 时无法关闭 thinking，四种方式实测全败
（请求体 `think:false` 静默忽略、Modelfile `PARAMETER think` 报未知参数、
`SYSTEM /no_think` 无效、用户消息加 `/no_think` 无效）。

**状态**：**已决策保留**（2026-08-30）。M0 是 dev/CI 档、不进对比结论，
换取统一走 OpenAI 兼容接口。

**连带约束**：`config/models.yaml` 里 M0 的 `max_tokens_floor` 必须 ≥ 800。
实测 `max_tokens=80` 时 thinking 吃光预算，`finish_reason=length` 且
**content 为空**。网关会自动把低于地板值的请求抬上去。
