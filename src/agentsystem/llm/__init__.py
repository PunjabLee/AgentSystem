"""模型调用层（宪法第六条：模型调用一律经 LLMGateway）。

本包**不得** import ``audit`` 或 ``db`` —— 模型层不知道审计存在，
埋点由 ``gateway`` 层调用装饰器完成。反向依赖会让 LLMGateway 无法独立测试。
"""

from agentsystem.llm.config import ModelConfigError, ModelsConfig, TierConfig, get_models_config
from agentsystem.llm.gateway import AllTiersUnavailable, LLMGateway
from agentsystem.llm.types import LLMResult, Message, ToolCall, ToolDef

__all__ = [
    "AllTiersUnavailable",
    "LLMGateway",
    "LLMResult",
    "Message",
    "ModelConfigError",
    "ModelsConfig",
    "TierConfig",
    "ToolCall",
    "ToolDef",
    "get_models_config",
]
