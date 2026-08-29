# 查证手法

评审质量的下限由查证能力决定。**版本、API 能力、生命周期**这三类断言永远不要凭记忆——它们变得快，错了代价高，而查证成本只有几十秒。

---

## 1. 版本与生命周期

全部返回 JSON，可直接管进 `python3 -c`。

| 目标 | 端点 | 取什么 |
|---|---|---|
| Python 包 | `https://pypi.org/pypi/<pkg>/json` | `info.version`、`info.requires_python`、`urls[].filename` |
| npm 包 | `https://registry.npmjs.org/<pkg>` | `dist-tags`、`versions[v].engines`、`versions[v].peerDependencies` |
| GitHub 正式版 | `https://api.github.com/repos/<o>/<r>/releases?per_page=20` | 过滤 `prerelease`/`draft` 后取 `tag_name`、`published_at` |
| GitHub tag（兜底） | `https://api.github.com/repos/<o>/<r>/tags` | 只发 tag 不发 Release 的项目用这个 |
| 生命周期 / EOL | `https://endoflife.date/api/<product>.json` | `cycle`、`latest`、`releaseDate`、`eol` |
| Docker 镜像标签 | `https://hub.docker.com/v2/repositories/<ns>/<repo>/tags?page_size=100&ordering=last_updated` | `results[].name` |
| 源码文件 | `https://raw.githubusercontent.com/<o>/<r>/<branch>/<path>` | 直接读定义 |

`endoflife.date` 的产品 slug：`python` `nodejs` `postgresql` `ubuntu` `debian` `redis` `kubernetes` `java` `go` `rust` `django` `nginx` 等。**回答「该用哪个 OS / 运行时版本」时，它比任何记忆都可靠**——能同时给出发布日、最新小版本和 EOL。

### 并发查询骨架

```python
import json, subprocess, concurrent.futures as cf
def curl(u):
    return subprocess.run(["curl","-sL","--max-time","30","-H","User-Agent: audit",u],
                          capture_output=True, text=True).stdout
def pypi(n):
    d = json.loads(curl(f"https://pypi.org/pypi/{n}/json"))["info"]
    return (n, d["version"], d.get("requires_python") or "-")
with cf.ThreadPoolExecutor(20) as ex:
    for r in ex.map(pypi, ["fastapi","langgraph","pydantic","asyncpg"]):
        print(r)
```

---

## 2. 四个真实的坑

**① macOS 上 Python 的 `urllib` 缺 CA 证书**
`urlopen` 全部报 `CERTIFICATE_VERIFY_FAILED`，而 `curl` 是通的。**直接用 `subprocess` 调 `curl`**，别去装证书。

**② wheel 标签检测会漏 ABI 无关的轮子**
判断某包是否支持某 Python 版本时，只 grep `cp3\d+` 会把 `ruff`、`playwright` 这类误报为"无 wheel"——它们发的是 `ruff-0.16.5-py3-none-macosx_11_0_arm64.whl`，**`py3-none` 但平台相关**，既不匹配 `cp3XX` 也不匹配 `-py3-none-any.whl`。

```python
tags = set(re.findall(r"cp3(\d+)", fname))          # ABI 相关
abi_agnostic = "-py3-none-" in fname                 # 含平台相关的 py3-none-<plat>
```

**③ 只发 tag 不发 Release 的项目**
`releases/latest` 返回空或报错，不代表没有新版。**回退到 `/tags`**。例：`pgvector` 全部以 git tag 发布。

**④ 动态渲染的厂商文档抓不到**
云厂商的产品文档页多为前端渲染，`curl` 拿不到版本列表。**明确记为"未查证"并列入待决事项，不要凭印象断言。**

---

## 3. 查 API 能力：往下看一层

这是评审中最容易出错的一类查证。

**失败模式**：查了端点的入参模型，发现顶层字段里没有目标能力，就断言"不支持"。

**真实案例**：某检索 API 的 `HitTestingPayload` 只有 `query`、`retrieval_model`、`external_retrieval_model`、`attachment_ids` 四个顶层字段，看不到元数据过滤。但：

```python
class RetrievalModel(BaseModel):
    ...
    metadata_filtering_conditions: MetadataFilteringCondition | None = Field(...)
```

**能力嵌在参数对象内部，是一等公民。** 一条错误的"不支持"结论，差点换来 0.5 人天和一层多余架构。

**正确流程**：
1. 找到 controller / 路由的入参模型
2. **对每个非标量字段，继续展开它的定义**
3. 找到 service 层，看它实际读了哪些键——`.get("some_key")` 这类动态读取不会出现在类型定义里
4. 三层都没有，才能说"不支持"

**并行查证多条路径**（文件可能改名或移位）：

```bash
for p in "api/controllers/service_api/x.py" "api/controllers/console/x.py" "api/services/x_service.py"; do
  out=$(curl -sL --max-time 20 "https://raw.githubusercontent.com/<o>/<r>/main/$p")
  [ -n "$out" ] && echo "--- 命中 $p ---" && echo "$out" | grep -nE "关键词" | head
done
```

枚举类能力（支持哪些后端 / 哪些驱动 / 哪些格式）通常有集中定义，直接读枚举比读文档快且准。

---

## 4. 文档自洽的机械检查

跨章节**语义**矛盾靠机械检查抓不到（那要靠人读），但下面四类漂移可以：

```bash
F=path/to/design.md

# ① 版本漂移：同一技术在不同处写了不同版本
grep -nE "(React|Node|Python|PostgreSQL|TypeScript) ?[0-9]+" $F | sort -t: -k2

# ② 基准文档的版本引用是否同步
grep -rn "v[0-9]\+\.[0-9]\+" docs/CONSTITUTION.md docs/*.md | grep -i "关联\|基于\|对应"

# ③ 未赋值占位符
grep -nE "【[^】]*】|<N>|TBD|TODO|\bN 秒|\bX 个|\bN 个" $F

# ④ 章节编号连续性
grep -oE '^## [0-9]+\.' $F | grep -oE '[0-9]+' | awk '{if($1!=p+1 && NR>1) print "断裂于 "$1; p=$1}'

# ⑤ 交叉引用目标是否存在
grep -oE '§[0-9]+(\.[0-9]+)?' $F | sort -u | while read r; do
  n=${r#§}; grep -qE "^#{2,4} ${n%%.*}\." $F || echo "悬空引用: $r"
done
```

**枚举一致性**需要人工对照——从 DDL 注释里抽出枚举值，grep 正文里的实际使用。今天的实例：DDL 注释写 `success/failed/degraded`，正文用了第四个值 `cancelled`。

---

## 5. 自查的盲区

一次自查如果只做了"占位符 / 算术 / 编号"这三类**机械检查**，会漏掉最贵的一类：**跨章节语义矛盾**。

今天两处都是这么漏的：
- 同一组件在两章里归属不同（一章说归应用层，另一章说归外部服务托管）
- 基准文档与主文档对同一接口的要求相反（一份说不声明写方法，一份声明了五个）

**机械检查抓不到语义矛盾。** 自查清单里必须显式包含一条：**列出所有跨章节引用的概念（组件归属、接口契约、数据流向、职责边界），逐一核对两处说法是否一致。**

---

## 6. 什么时候停止查证

评审 agent 掉线的主因是无限重试外部查证。给自己和评审者同一条规则：

**同一事实最多换两条路径查证。两次都拿不到，写进「我不确定的地方」并给出验证方法与耗时估计（例如"起一次实例打一条 API，30 分钟可确认"），然后继续。**

一条标注清楚的未知项，比一条查了半小时仍不确定却写成结论的断言有价值得多。
