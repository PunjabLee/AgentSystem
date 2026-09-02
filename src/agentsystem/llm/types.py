"""LLMGateway 的数据契约（P1 详细设计 §2.2）。

本模块刻意不 import ``audit`` 或 ``db``：模型层不知道审计存在，埋点由
``gateway`` 层调用装饰器完成。反向依赖会让 LLMGateway 无法独立测试。
"""

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True, slots=True)
class Message:
    """一条对话消息。"""

    role: Role
    content: str
    tool_call_id: str | None = None
    name: str | None = None

    def to_openai(self) -> dict[str, Any]:
        """转成 OpenAI 兼容的消息体，省略空字段。"""
        out: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_call_id is not None:
            out["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            out["name"] = self.name
        return out


@dataclass(frozen=True, slots=True)
class ToolDef:
    """工具定义（OpenAI function calling 形状）。"""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        """转成 tools 数组的一项。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True, slots=True)
class ToolCall:
    """模型请求的一次工具调用。

    ``arguments`` 保留**原始字符串**而不在此处 json.loads：模型可能吐出
    非法 JSON，解析失败属于业务层要处理的情形，不该让整个网关调用崩掉。
    """

    id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class LLMResult:
    """一次模型调用的完整结果。

    ``tool_calls`` 用空列表而非 None：调用方写 ``if result.tool_calls:``
    即可，不必先判空。
    """

    content: str | None
    finish_reason: str
    tier_used: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    #: 非 None 表示发生过降级，供 UI 与审计打标。
    degraded_from: str | None = None

    # ── 以下四项喂给 audit_log，不在此处落库 ──
    latency_ms: int = 0
    #: 首 token 时延，仅流式有值。
    ttft_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
