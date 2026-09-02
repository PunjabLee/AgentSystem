"""LLMGateway 的逻辑断言（P1 详细设计 §2）。

**全部走 mock，不发真实请求** —— CI 已定为不含 LLM 调用（P1.5.1）。
真实连通性由 `make chat` 手动冒烟负责，两者分工明确：这里测的是档位选择、
extra_body 注入、地板值抬升与降级链，都是与模型无关的纯逻辑。
"""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import APIConnectionError, APIStatusError

from agentsystem.llm import LLMGateway, Message, ToolDef
from agentsystem.llm.config import ModelConfigError, ModelsConfig, TierConfig
from agentsystem.llm.gateway import AllTiersUnavailable


def _tier(
    name: str,
    *,
    role: str = "benchmark",
    floor: int = 512,
    extra: dict | None = None,
    unavailable: str | None = None,
) -> TierConfig:
    return TierConfig(
        tier=name,
        name=f"档位 {name}",
        base_url=f"http://127.0.0.1:1{name[-1]}000/v1",
        model=f"model-{name}",
        role=role,  # type: ignore[arg-type]
        max_tokens_floor=floor,
        extra_body=extra or {},
        api_key="k",
        unavailable_reason=unavailable,
    )


def _config(**overrides: Any) -> ModelsConfig:
    tiers = {
        "M0": _tier("M0", role="dev", floor=800),
        "M1": _tier("M1", extra={"chat_template_kwargs": {"enable_thinking": False}}),
        "M2": _tier("M2", extra={"chat_template_kwargs": {"enable_thinking": False}}),
        "M3": _tier("M3", extra={"enable_thinking": False}),
        "M4": _tier("M4", extra={"thinking": {"type": "disabled"}}),
    }
    base = ModelsConfig(default_tier="M4", tiers=tiers, fallback_chain=("M1", "M2", "M3"))
    return replace(base, **overrides)


class FakeClient:
    """记录收到的 payload，并按脚本决定成功或抛错。"""

    def __init__(self, tier: str, behaviour: dict[str, Any], captured: list[dict]) -> None:
        self._tier = tier
        self._behaviour = behaviour
        self._captured = captured
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **payload: Any) -> Any:
        self._captured.append({"tier": self._tier, **payload})
        outcome = self._behaviour.get(self._tier)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=f"来自 {self._tier}", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20),
        )


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch):
    """造一个把真实客户端换成 FakeClient 的网关工厂。"""

    def build(behaviour: dict[str, Any] | None = None, config: ModelsConfig | None = None):
        captured: list[dict] = []
        gw = LLMGateway(config or _config())
        monkeypatch.setattr(
            LLMGateway, "_client", lambda self, cfg: FakeClient(cfg.tier, behaviour or {}, captured)
        )
        return gw, captured

    return build


MSGS = [Message(role="user", content="查一下库存")]


# ── extra_body：档位差异的唯一收敛点 ──────────────────────────
@pytest.mark.parametrize(
    ("tier", "expected"),
    [
        ("M0", None),
        ("M1", {"chat_template_kwargs": {"enable_thinking": False}}),
        ("M3", {"enable_thinking": False}),
        ("M4", {"thinking": {"type": "disabled"}}),
    ],
)
async def test_extra_body_is_injected_per_tier(harness, tier: str, expected: dict | None) -> None:
    """四档形状互不通用，且全部来自配置 —— 代码里没有 if tier == 分支。"""
    gw, captured = harness()
    await gw.chat(MSGS, tier=tier)
    assert captured[0].get("extra_body") == expected


def test_gateway_has_no_per_tier_branches() -> None:
    """🔴 机械检查：gateway.py 不得出现按档位名分支。

    差异一旦渗进代码，新增档位就要改代码而非改配置，而「改代码」意味着
    每加一档都要重跑全部回归。
    """
    import ast
    import pathlib

    # 走 AST 而非文本匹配：本文件与 gateway.py 的注释里都写着
    # 「不含任何 if tier == "M3" 分支」这句说明，纯文本检查会被自己的
    # 文档绊倒 —— 一个只会误报的检查最终会被人关掉。
    tree = ast.parse(pathlib.Path("src/agentsystem/llm/gateway.py").read_text(encoding="utf-8"))
    tier_names = {"M0", "M1", "M2", "M3", "M4"}
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        for operand in node.comparators
        if isinstance(operand, ast.Constant) and operand.value in tier_names
    ]
    assert not offenders, f"gateway.py 第 {offenders} 行出现了按档位名分支"


# ── max_tokens 地板 ───────────────────────────────────────────
async def test_max_tokens_raised_to_floor(harness) -> None:
    """低于地板值自动抬升 —— M0 实测 max_tokens=80 时 content 为空。"""
    gw, captured = harness()
    await gw.chat(MSGS, tier="M0", max_tokens=80)
    assert captured[0]["max_tokens"] == 800


async def test_max_tokens_above_floor_is_respected(harness) -> None:
    gw, captured = harness()
    await gw.chat(MSGS, tier="M0", max_tokens=4096)
    assert captured[0]["max_tokens"] == 4096


# ── 降级链 ────────────────────────────────────────────────────
async def test_fallback_walks_chain_and_marks_degradation(harness) -> None:
    """M1 连不上 → M2 连不上 → M3 成功，且标出降级来源。"""
    unreachable = APIConnectionError(request=httpx.Request("POST", "http://x"))
    gw, captured = harness({"M1": unreachable, "M2": unreachable})
    result = await gw.chat(MSGS, tier="M1")
    assert [c["tier"] for c in captured] == ["M1", "M2", "M3"]
    assert result.tier_used == "M3"
    assert result.degraded_from == "M1", "降级来源要能落进 audit_log 与响应元数据"


async def test_no_degradation_marker_on_direct_success(harness) -> None:
    gw, _ = harness()
    assert (await gw.chat(MSGS, tier="M1")).degraded_from is None


async def test_4xx_does_not_trigger_fallback(harness) -> None:
    """🔴 4xx 是配置或请求错误，回落只会同样失败，且掩盖真实原因。"""
    bad_request = APIStatusError(
        "bad request",
        response=httpx.Response(400, request=httpx.Request("POST", "http://x")),
        body=None,
    )
    gw, captured = harness({"M1": bad_request})
    with pytest.raises(APIStatusError):
        await gw.chat(MSGS, tier="M1")
    assert [c["tier"] for c in captured] == ["M1"], "不应尝试其他档位"


async def test_5xx_does_trigger_fallback(harness) -> None:
    """5xx 是服务端故障，换一档有意义。"""
    server_error = APIStatusError(
        "boom",
        response=httpx.Response(503, request=httpx.Request("POST", "http://x")),
        body=None,
    )
    gw, captured = harness({"M1": server_error})
    assert (await gw.chat(MSGS, tier="M1")).tier_used == "M2"
    assert [c["tier"] for c in captured] == ["M1", "M2"]


async def test_unavailable_tiers_are_skipped(harness) -> None:
    """未开通的档（如等 D1 的 M1/M2）直接跳过，不浪费一次超时。"""
    cfg = _config()
    cfg = replace(
        cfg,
        tiers={
            **cfg.tiers,
            "M1": _tier("M1", unavailable="环境变量未设置"),
            "M2": _tier("M2", unavailable="环境变量未设置"),
        },
    )
    gw, captured = harness(config=cfg)
    result = await gw.chat(MSGS, tier="M1")
    assert [c["tier"] for c in captured] == ["M3"]
    assert result.degraded_from == "M1"


async def test_all_tiers_failing_reports_each_reason(harness) -> None:
    """全链失败要报出逐档原因，否则只知道"不能用"、不知道为什么。"""
    unreachable = APIConnectionError(request=httpx.Request("POST", "http://x"))
    gw, _ = harness({t: unreachable for t in ("M1", "M2", "M3")})
    with pytest.raises(AllTiersUnavailable) as e:
        await gw.chat(MSGS, tier="M1")
    assert set(e.value.failures) == {"M1", "M2", "M3"}


# ── 工具与配置校验 ────────────────────────────────────────────
async def test_tools_are_serialised(harness) -> None:
    gw, captured = harness()
    tool = ToolDef(name="query_inventory", description="查库存", parameters={"type": "object"})
    await gw.chat(MSGS, tier="M4", tools=[tool])
    assert captured[0]["tools"][0]["function"]["name"] == "query_inventory"


def test_plaintext_api_key_is_refused(tmp_path) -> None:
    """🔴 安全评审阻塞项：models.yaml 被 git 跟踪，明文密钥必须拒绝启动。"""
    from agentsystem.llm.config import load_models_config

    f = tmp_path / "models.yaml"
    f.write_text(
        'default_tier: M4\ntiers:\n  M4:\n    name: x\n    base_url: "http://x/v1"\n'
        '    model: m\n    api_key: "sk-real-secret-value"\nfallback_chain: []\n',
        encoding="utf-8",
    )
    with pytest.raises(ModelConfigError, match="疑似明文密钥"):
        load_models_config(f)


def test_dev_tier_in_fallback_chain_is_refused(tmp_path) -> None:
    """dev 档进降级链 → 一次静默回落就让对比报告里的数字不再可比。"""
    from agentsystem.llm.config import load_models_config

    f = tmp_path / "models.yaml"
    f.write_text(
        'default_tier: M0\ntiers:\n  M0:\n    name: x\n    base_url: "http://x/v1"\n'
        "    model: m\n    api_key: null\n    role: dev\nfallback_chain: [M0]\n",
        encoding="utf-8",
    )
    with pytest.raises(ModelConfigError, match="dev 档不得进入"):
        load_models_config(f)


def test_using_unavailable_tier_reports_root_cause() -> None:
    """用一个未开通的档时，报的必须是根因，而不是调用时才出现的 401。"""
    cfg = _config()
    cfg = replace(cfg, tiers={**cfg.tiers, "M1": _tier("M1", unavailable="环境变量 X 未设置")})
    with pytest.raises(ModelConfigError, match="环境变量 X 未设置"):
        cfg.get("M1")
