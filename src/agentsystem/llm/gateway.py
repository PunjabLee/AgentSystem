"""模型调用统一抽象层（宪法第六条的落地物，P1 详细设计 §2.2–2.4）。

职责：档位选择、``extra_body`` 注入、超时重试、降级回落、用量计量。
**不负责**：审计落库（由 gateway 层的装饰器完成）、业务语义。

四档的 thinking 开关形状互不通用，但差异**全部收敛在 models.yaml 的
extra_body 段** —— 本模块不含任何 ``if tier == "M3"`` 分支，新增档位只改配置。
"""

import logging
import time
from typing import Any

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from agentsystem.llm.config import ModelsConfig, TierConfig, get_models_config
from agentsystem.llm.types import LLMResult, Message, ToolCall, ToolDef

logger = logging.getLogger(__name__)

#: 单次请求超时。自建档走 SSH 隧道，首 token 可能较慢，故给得比常规宽。
DEFAULT_TIMEOUT_S = 120.0
#: 同一档位内的重试次数。跨档回落由降级链负责，两者不叠加 ——
#: 否则一次调用最坏要等 3 档 × 3 次 × 120s。
RETRIES_PER_TIER = 1


class AllTiersUnavailable(Exception):
    """降级链走完仍无可用档位。附带每档的失败原因，便于定位。"""

    def __init__(self, failures: dict[str, str]) -> None:
        """记录逐档失败原因。"""
        self.failures = failures
        detail = "；".join(f"{t}: {e}" for t, e in failures.items())
        super().__init__(f"全部档位不可用 —— {detail}")


class LLMGateway:
    """统一的模型调用入口。"""

    def __init__(self, config: ModelsConfig | None = None) -> None:
        """按配置建立各档客户端。客户端按档缓存，避免每次调用重建连接池。"""
        self._config = config or get_models_config()
        self._clients: dict[str, AsyncOpenAI] = {}

    def _client(self, cfg: TierConfig) -> AsyncOpenAI:
        """取或建某档的客户端。"""
        if cfg.tier not in self._clients:
            self._clients[cfg.tier] = AsyncOpenAI(
                base_url=cfg.base_url,
                # Ollama 不鉴权但 SDK 要求非空，给个哑值；真实密钥来自环境变量。
                api_key=cfg.api_key or "not-needed",
                timeout=DEFAULT_TIMEOUT_S,
                max_retries=RETRIES_PER_TIER,
            )
        return self._clients[cfg.tier]

    def _chain_for(self, tier: str) -> list[str]:
        """算出从 ``tier`` 开始的实际尝试顺序。

        规则：请求档排第一；其后接降级链中排在它之后的档位；不可用的档
        直接跳过（它们的失败原因由配置层记录，不必每次调用都重试一遍）。
        M0 是 dev 档，不在链中 —— 静默回落到它会污染对比结论。
        """
        chain = [tier]
        if tier in self._config.fallback_chain:
            idx = self._config.fallback_chain.index(tier)
            chain += [t for t in self._config.fallback_chain[idx + 1 :]]
        return [t for t in chain if self._config.tiers.get(t) and self._config.tiers[t].available]

    async def chat(
        self,
        messages: list[Message],
        *,
        tier: str | None = None,
        tools: list[ToolDef] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResult:
        """发起一次对话调用，必要时沿降级链回落。

        Args:
            messages: 对话消息。
            tier: 目标档位；None 用配置里的 ``default_tier``。
            tools: 工具定义；None 表示本次不带工具。
            max_tokens: 上限；低于该档 ``max_tokens_floor`` 时自动抬升。
            temperature: 采样温度，评测默认 0。

        Returns:
            调用结果。发生回落时 ``tier_used`` 与 ``degraded_from`` 不同。

        Raises:
            AllTiersUnavailable: 链上每一档都失败。
        """
        requested = tier or self._config.default_tier
        chain = self._chain_for(requested)
        if not chain:
            raise AllTiersUnavailable(
                {requested: self._config.tiers[requested].unavailable_reason or "不在配置中"}
                if requested in self._config.tiers
                else {requested: "未知档位"}
            )

        failures: dict[str, str] = {}
        for candidate in chain:
            cfg = self._config.tiers[candidate]
            try:
                result = await self._call_once(cfg, messages, tools, max_tokens, temperature)
            except (APIConnectionError, APITimeoutError, httpx.TransportError) as exc:
                # 连不上或超时 —— 换一档有意义。
                failures[candidate] = f"{type(exc).__name__}: {exc}"
                logger.warning("档位 %s 不可达，尝试回落：%s", candidate, exc)
                continue
            except APIStatusError as exc:
                if exc.status_code < 500:
                    # 4xx 是配置或请求错误，回落到别的档只会同样失败，
                    # 且会掩盖真实原因。直接抛。
                    raise
                failures[candidate] = f"HTTP {exc.status_code}"
                logger.warning("档位 %s 返回 %s，尝试回落", candidate, exc.status_code)
                continue

            if candidate != requested:
                return _with_degradation(result, degraded_from=requested)
            return result

        raise AllTiersUnavailable(failures)

    async def _call_once(
        self,
        cfg: TierConfig,
        messages: list[Message],
        tools: list[ToolDef] | None,
        max_tokens: int | None,
        temperature: float,
    ) -> LLMResult:
        """向单一档位发起一次调用，不含降级逻辑。"""
        # 低于地板值会让 thinking 吃光预算、content 为空（M0 实测 max_tokens=80
        # 时 finish_reason=length 且 content 空）。抬升而非报错：调用方给的是
        # 期望上限，不是硬约束。
        effective_max = max(max_tokens or cfg.max_tokens_floor, cfg.max_tokens_floor)

        payload: dict[str, Any] = {
            "model": cfg.model,
            "messages": [m.to_openai() for m in messages],
            "max_tokens": effective_max,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = [t.to_openai() for t in tools]
        if cfg.extra_body:
            # 档位差异的唯一注入点。
            payload["extra_body"] = dict(cfg.extra_body)

        started = time.perf_counter()
        response = await self._client(cfg).chat.completions.create(**payload)
        latency_ms = int((time.perf_counter() - started) * 1000)

        choice = response.choices[0]
        usage = response.usage
        return LLMResult(
            content=choice.message.content,
            finish_reason=choice.finish_reason or "stop",
            tier_used=cfg.tier,
            tool_calls=[
                ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
                for tc in (choice.message.tool_calls or [])
            ],
            latency_ms=latency_ms,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
        )


def _with_degradation(result: LLMResult, *, degraded_from: str) -> LLMResult:
    """给结果打上降级标记。

    ``LLMResult`` 是 frozen 的，故重建而非改字段 —— 这正是 frozen 想要的：
    降级信息只能由网关在此处一次性写入，下游改不动。
    """
    return LLMResult(
        content=result.content,
        finish_reason=result.finish_reason,
        tier_used=result.tier_used,
        tool_calls=result.tool_calls,
        degraded_from=degraded_from,
        latency_ms=result.latency_ms,
        ttft_ms=result.ttft_ms,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    )
