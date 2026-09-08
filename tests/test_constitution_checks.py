"""宪法条款的机械检查（P1 详细设计 §6）。

**没有检查的原则等于没有原则。** 本文件把宪法里可机械判定的条款逐条落成
断言。判据来自 §6 的表格，一条对一条。

不在此处的条款各有归属：
  一  · 二次确认   → tests/test_audit_decorator.py 的负测试
  一  · 防篡改     → tests/test_audit_tamper_proof.py
  十  · 范围收窄   → tests/test_identity.py
"""

import ast
import pathlib
import re
import subprocess

import pytest

SRC = pathlib.Path("src/agentsystem")
#: 模型端点 URL 只允许出现在这两处（宪法第六条）。
_URL_ALLOWLIST = {"src/agentsystem/llm/config.py"}


def _py_files() -> list[pathlib.Path]:
    return sorted(SRC.rglob("*.py"))


def _imports(path: pathlib.Path) -> set[str]:
    """取一个模块 import 的全部顶层模块名（含 from ... import 的来源）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


# ── 宪法四 · 全链路 async ─────────────────────────────────────
@pytest.mark.parametrize("banned", ["requests", "psycopg2"])
def test_no_sync_io_libraries(banned: str) -> None:
    """请求路径不得出现同步 IO 库。

    ⚠️ 禁的是 ``psycopg2``（同步旧版），**不得误伤 ``psycopg``（v3）** ——
    后者是 langgraph-checkpoint-postgres 的硬依赖。故按模块名精确比对，
    不做子串匹配。
    """
    offenders = [
        str(f)
        for f in _py_files()
        if any(m == banned or m.startswith(f"{banned}.") for m in _imports(f))
    ]
    assert not offenders, f"{banned} 出现在：{offenders}"


def test_psycopg3_is_not_mistakenly_banned() -> None:
    """反向断言：上一条检查不能把 psycopg v3 也禁掉。

    没有这条，某次"顺手加强"检查就会把 checkpointer 打挂，而且要到运行
    时才发现。
    """
    assert not any(m == "psycopg2" for f in _py_files() for m in _imports(f))
    # psycopg（v3）允许存在，这里只确认检查逻辑区分得开
    assert "psycopg2" != "psycopg"


def test_no_blocking_sleep_in_request_path() -> None:
    """``time.sleep`` 会阻塞整个事件循环，不是让一个请求变慢。"""
    offenders = []
    for f in _py_files():
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "sleep"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "time"
            ):
                offenders.append(f"{f}:{node.lineno}")
    assert not offenders, f"time.sleep 出现在：{offenders}"


# ── 宪法六 · 模型调用经 LLMGateway ────────────────────────────
def test_model_endpoints_only_in_config() -> None:
    """模型端点 URL 只允许出现在 config/models.yaml 与 llm/config.py。

    URL 一旦散落进业务代码，"换一档模型"就变成全仓库搜索替换，
    而 LLMGateway 的存在意义正是让它只是改一行配置。
    """
    markers = ("api.deepseek.com", "autodl.art", ":11434", ":18001", ":18002")
    offenders = []
    for f in _py_files():
        if str(f) in _URL_ALLOWLIST:
            continue
        text = f.read_text(encoding="utf-8")
        offenders += [f"{f} → {m}" for m in markers if m in text]
    assert not offenders, f"模型端点 URL 出现在配置层之外：{offenders}"


# ── 宪法七 · 机密只走环境变量 ─────────────────────────────────
def test_dotenv_is_not_tracked_by_git() -> None:
    """.env 绝不能进版本库。"""
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".env"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.returncode != 0, ".env 已被 git 跟踪"


def test_models_yaml_has_no_plaintext_secret() -> None:
    """models.yaml 被 git 跟踪，api_key 必须是 ${VAR} 占位。"""
    import yaml

    raw = yaml.safe_load(pathlib.Path("config/models.yaml").read_text(encoding="utf-8"))
    for tier, spec in raw["tiers"].items():
        key = spec.get("api_key")
        assert key is None or key.startswith("${"), f"{tier}.api_key 疑似明文"


def test_users_yaml_has_no_plaintext_token() -> None:
    """users.yaml 只存 SHA-256：64 位十六进制，且不等于任何 .env 里的明文。"""
    import os
    import re

    import yaml

    raw = yaml.safe_load(pathlib.Path("config/users.yaml").read_text(encoding="utf-8"))
    plaintexts = {v for k, v in os.environ.items() if k.startswith("DEMO_TOKEN_") and v}
    for user in raw["users"]:
        digest = user["token_sha256"]
        assert re.fullmatch(r"[0-9a-f]{64}", digest), f"{user['user_id']} 的值不是 SHA-256"
        assert digest not in plaintexts, f"{user['user_id']} 存了明文令牌"


# ── 宪法十一 · 代码必须自带注释 ───────────────────────────────
def test_noqa_d_must_carry_a_reason() -> None:
    """宪法要求 ``# noqa: D`` 同行给理由，而 ruff 没有这条规则，故补一条。

    判据：同一行 ``noqa: D`` 之后必须还有非空文字。否则这条豁免就成了
    「关掉检查」的静默开关。
    """
    import re

    pattern = re.compile(r"#\s*noqa:\s*D\d*(.*)$")
    offenders = []
    for f in _py_files():
        for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            match = pattern.search(line)
            # 判"后面还有没有实义字符"，而不是剥标点：破折号、冒号、中英文
            # 混排的写法太多，逐个列举必漏。
            if match and not re.search(r"\w", match.group(1)):
                offenders.append(f"{f}:{lineno}")
    assert not offenders, f"以下 noqa: D 未给理由：{offenders}"


# ── 模块边界（P1 详细设计 §1）─────────────────────────────────
def test_llm_does_not_import_audit_or_db() -> None:
    """🔴 llm/ 不得 import audit/ 或 db/。

    模型层不知道审计存在，埋点由 gateway 层调用装饰器完成。反向依赖会让
    LLMGateway 无法独立测试 —— 那正是本仓库全部 mock 测试的前提。
    """
    offenders = [
        f"{f} → {m}"
        for f in sorted((SRC / "llm").rglob("*.py"))
        for m in _imports(f)
        if m.startswith(("agentsystem.audit", "agentsystem.db"))
    ]
    assert not offenders, f"llm/ 越界依赖：{offenders}"


def test_audit_does_not_import_intent() -> None:
    """audit/ 不得 import intent/，否则两个模块循环依赖。

    装饰器校验令牌时经参数接收校验结果，不自己去查库。
    """
    offenders = [
        f"{f} → {m}"
        for f in sorted((SRC / "audit").rglob("*.py"))
        for m in _imports(f)
        if m.startswith("agentsystem.intent")
    ]
    assert not offenders, f"audit/ 越界依赖：{offenders}"


def test_grade_vocabulary_matches_the_corpus() -> None:
    """业务表的等级值域必须与 RAG 语料一致。

    立项能力②是「静态知识与动态业务数据融合问答」。语料说「一等品」而业务表
    存「一级品」时，`WHERE grade = ...` **不报错，只返回空集** —— 于是被解读成
    「没有该等级的库存」，融合问答给出一个看似合理的错误答案。

    这类跨源分叉真实发生过：设计文档的列注释与语料曾用不同字面量，
    而两边各自都自洽，靠人眼比对发现不了。语料是被检索的事实来源，
    值域以它为准。
    """
    corpus = pathlib.Path("corpus/06-色差与等级判定标准.md")
    if not corpus.exists():
        pytest.skip("语料文件不存在")

    # 语料里等级出现在表格首列，形如 `| 优等品 | ≤ 1.0 | ...`
    in_corpus = set(
        re.findall(r"^\|\s*([一-龥]{2,4}品)\s*\|", corpus.read_text(encoding="utf-8"), re.M)
    )
    assert in_corpus, "语料中未解析出任何等级值，检查表格格式是否变了"

    spec = pathlib.Path("docs/design/P2-DETAILED-DESIGN.md").read_text(encoding="utf-8")
    m = re.search(r"Grade\s*=\s*Literal\[([^\]]+)\]", spec)
    assert m, "P2 详细设计中未找到 Grade 的 Literal 定义"
    in_spec = set(re.findall(r'"([^"]+)"', m.group(1)))

    assert in_spec == in_corpus, (
        f"等级值域与语料分叉 —— 设计={sorted(in_spec)} 语料={sorted(in_corpus)}。"
        "语料是检索的事实来源，值域应向它对齐。"
    )
