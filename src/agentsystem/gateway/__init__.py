"""入口网关：身份、会话与请求上下文（宪法第五条：不绕过 Gateway）。"""

from agentsystem.gateway.context import RequestContext, current_context, set_context
from agentsystem.gateway.identity import IdentityRegistry, get_identity_registry

__all__ = [
    "IdentityRegistry",
    "RequestContext",
    "current_context",
    "get_identity_registry",
    "set_context",
]
