"""请求级身份上下文（P1 详细设计 §2.5.2）。

**为什么用 contextvar 而不是函数参数**：db 层要在每次查询时强制注入范围
过滤（P2 §5.1）。若靠参数传递，每个端点、每个查询函数都要记得传，
**漏一个就是越权**；contextvar 让 db 层自己取，忘不掉。

代价是隐式依赖。用两条约束抵消：``frozen=True`` 让下游改不动，
``set_context`` 只在中间件调用（CI 有机械检查，见 §6）。
"""

from contextvars import ContextVar, Token
from dataclasses import dataclass

from agentsystem.errors import AuthError

#: 表示"不限"的通配符。仅用于 regions —— bu_codes 不设通配，
#: 事业部边界必须逐个列举，避免一次配置疏忽就打通三个事业部。
WILDCARD = "*"


@dataclass(frozen=True, slots=True)
class RequestContext:
    """请求级身份上下文。由 Gateway 中间件构造，全链路只读。

    ``frozen=True`` 是刻意的 —— 任何下游代码都不得修改身份或权限范围。
    宪法第一条的操作者、第十条的范围上界、write_intent 的会话绑定，
    三处都从这一个对象取，不允许任何一处另起炉灶。
    """

    user_id: str
    #: 服务端生成。客户端可指定即可冒充他人会话完成 write_intent 确认。
    session_id: str
    #: 服务端生成的 UUIDv7。客户端可指定即可让两次操作共用一个 id，
    #: 污染 attempt/outcome 的串联。
    trace_id: str
    #: 权限上界，宪法第十条的 allowed 集合。
    bu_codes: frozenset[str]
    regions: frozenset[str]
    #: 入站 X-Trace-Id 的降级留存，仅作参考，永不作为权威 trace_id。
    client_trace_id: str | None = None

    def narrow_bu(self, requested: frozenset[str] | None) -> frozenset[str]:
        """把请求的事业部范围收窄到授权上界内（宪法第十条）。

        三步语义：未指定则取全部授权；指定则取交集；交集为空即越权，
        **显式报错而非静默返回空结果** —— 静默让用户以为"确实没有数据"，
        而真相是"你无权看"，这两件事在业务上完全不同。

        Args:
            requested: 用户或模型请求的事业部集合；None 表示未指定。

        Returns:
            实际生效的事业部集合。

        Raises:
            AuthError: 请求范围与授权上界无交集。
        """
        if requested is None:
            return self.bu_codes
        effective = requested & self.bu_codes
        if not effective:
            raise AuthError(f"无权访问事业部 {sorted(requested)}", code="AUTH_BU_OUT_OF_SCOPE")
        return effective

    def narrow_region(self, requested: frozenset[str] | None) -> frozenset[str] | None:
        """把请求的区域范围收窄到授权上界内。

        Returns:
            生效区域；``None`` 表示不限（授权为通配且未指定）。
        """
        if WILDCARD in self.regions:
            return requested
        if requested is None:
            return self.regions
        effective = requested & self.regions
        if not effective:
            raise AuthError(f"无权访问区域 {sorted(requested)}", code="AUTH_REGION_OUT_OF_SCOPE")
        return effective


_CURRENT: ContextVar[RequestContext] = ContextVar("request_context")


def current_context() -> RequestContext:
    """取当前请求的上下文。

    Raises:
        LookupError: 未经中间件设置即调用。这是编程错误而非运行时状况，
            故不包装成 AppError —— 若它变成 500 返回给用户，说明有代码
            绕过了 Gateway（宪法第五条）。
    """
    return _CURRENT.get()


def set_context(ctx: RequestContext) -> Token[RequestContext]:
    """设置当前请求的上下文。**只应由 Gateway 中间件与测试夹具调用。**"""
    return _CURRENT.set(ctx)


def reset_context(token: Token[RequestContext]) -> None:
    """还原上下文。中间件在请求结束时调用，避免跨请求泄漏。"""
    _CURRENT.reset(token)
