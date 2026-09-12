"""入口中间件：鉴权 → 限流 → 上下文注入（P2.3.1，宪法第五条）。

## 为什么是纯 ASGI 中间件而不是 BaseHTTPMiddleware

两者都能把 contextvar 传到端点（已实测），但 ``BaseHTTPMiddleware`` 把下游应用
放进 anyio 的**子任务**里跑，子任务拿到的是 context 的**副本**。于是
``dispatch`` 里 ``set`` 能传下去，而请求结束时的 ``reset`` 作用在父 context 上 ——
看起来成对，实际没有还原子任务里的那一份。

纯 ASGI 中间件与端点同任务同 context，``try/finally`` 里的 ``reset`` 才真正生效。
``tests/test_gateway_middleware.py`` 同时钉住「能传下去」和「不跨请求泄漏」两条。

## 为什么鉴权不做成 FastAPI 依赖

依赖是**逐端点声明**的，少写一个 ``Depends`` 就是一个无鉴权端点，而且不会报错。
中间件对全部路径生效，豁免必须显式登记在 :data:`PUBLIC_PATHS` 里 ——
把「默认安全」换成了「例外要写出来」。这与 ``db/scope.py`` 把权限注入放在 db 层
是同一个理由：能漏的地方就会漏。
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

from agentsystem.errors import AppError, AuthError
from agentsystem.gateway.context import reset_context, set_context
from agentsystem.gateway.identity import authenticate, new_conversation_id
from agentsystem.gateway.ratelimit import ConcurrencyGate, TooManyRequests

#: 无需任何身份即可访问。健康检查要能在令牌配置坏掉时仍然可用 ——
#: 否则「服务是否活着」与「令牌是否正确」两个问题会纠缠在一起。
#: schema 端点公开是为了 P2.3.6 的 Dify 导入探针：导入器取 schema 时不带令牌。
PUBLIC_PATHS = frozenset({"/healthz", "/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"})

#: 需要令牌、但**不**需要 conversation_id 的路径。
#: 会话标识由服务端签发（``identity.new_conversation_id``），
#: 客户端第一次调用时手里还没有 —— 这是唯一的先有鸡问题，单独开一个口。
SESSION_BOOTSTRAP_PATHS = frozenset({"/v1/sessions"})

#: 入站头名。全部小写，ASGI 的 headers 是小写字节串。
_H_AUTH = b"authorization"
_H_CONVERSATION = b"x-conversation-id"
_H_CLIENT_TRACE = b"x-trace-id"


def _header(scope: dict[str, Any], name: bytes) -> str | None:
    """从 ASGI scope 取一个请求头。"""
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


def _bearer(scope: dict[str, Any]) -> str:
    """取出 Bearer 令牌。

    Raises:
        AuthError: 头缺失或不是 Bearer 形态。
    """
    raw = _header(scope, _H_AUTH)
    if raw is None:
        raise AuthError("缺少 Authorization 头", code="AUTH_TOKEN_MISSING")
    scheme, _, token = raw.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthError("Authorization 须为 Bearer 形态", code="AUTH_TOKEN_MALFORMED")
    return token


class GatewayMiddleware:
    """把一次 HTTP 请求变成一个带身份上下文的调用。

    顺序是**限流在鉴权之后**：令牌无效的请求不该占用闸门名额，否则一串错误
    请求就能把正常用户挤出去 —— 那是把限流器变成了拒绝服务的帮手。
    """

    def __init__(self, app: Callable[..., Awaitable[None]], *, gate: ConcurrencyGate) -> None:
        """包住下游 ASGI 应用。

        Args:
            app: 下游应用（FastAPI 实例）。
            gate: 并发闸门，与连接池大小配套取值。
        """
        self.app = app
        self._gate = gate

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """ASGI 入口。"""
        # lifespan、websocket 等非 http 事件原样透传：它们没有请求头，
        # 也不该被鉴权拦住。
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        if path in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        try:
            ctx = self._resolve(scope, path)
        except AppError as exc:
            # 鉴权失败时还没有权威 trace_id。用客户端传来的那个作降级标识 ——
            # 它不可信，但这里只是让用户报障时有个能对上的串，不进审计的
            # trace_id 字段。
            await self._reject(send, exc, trace_id=_header(scope, _H_CLIENT_TRACE) or "-")
            return

        decision = await self._gate.acquire()
        if not decision.allowed:
            await self._reject(
                send,
                TooManyRequests("服务繁忙，请稍后重试"),
                trace_id=ctx.trace_id,
                retry_after_s=decision.retry_after_s,
            )
            return

        token = set_context(ctx)
        try:
            await self.app(scope, receive, self._with_trace_id(send, ctx.trace_id))
        finally:
            # 两个 finally 动作缺一不可：闸门漏还会永久小一格，
            # 上下文漏则会让下一个复用同一任务的请求带着别人的身份跑。
            reset_context(token)
            await self._gate.release()

    def _resolve(self, scope: dict[str, Any], path: str) -> Any:
        """解析身份。会话引导路径不要求 conversation_id。"""
        bearer = _bearer(scope)
        client_trace = _header(scope, _H_CLIENT_TRACE)
        if path in SESSION_BOOTSTRAP_PATHS:
            # 用一个当场签发的标识占位：这次请求的产物就是给客户端一个真的，
            # 而本次请求自身不碰任何 write_intent，session_id 用不上。
            return authenticate(
                bearer, conversation_id=new_conversation_id(), client_trace_id=client_trace
            )
        conversation_id = _header(scope, _H_CONVERSATION)
        if conversation_id is None:
            raise AuthError(
                "缺少 X-Conversation-Id 头；先调用 POST /v1/sessions 领取",
                code="AUTH_NO_CONVERSATION",
            )
        return authenticate(bearer, conversation_id=conversation_id, client_trace_id=client_trace)

    @staticmethod
    def _with_trace_id(send: Any, trace_id: str) -> Any:
        """在响应头里回带 trace_id。

        让用户截图报障时手里就有审计表的检索键 —— 否则只能拿时间戳去猜。
        """

        async def wrapped(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-trace-id", trace_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        return wrapped

    @staticmethod
    async def _reject(
        send: Any, exc: AppError, *, trace_id: str, retry_after_s: int | None = None
    ) -> None:
        """直接写出错误包络，不进下游应用。"""
        body = json.dumps(exc.to_envelope(trace_id), ensure_ascii=False).encode("utf-8")
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"x-trace-id", trace_id.encode("latin-1")),
        ]
        if retry_after_s is not None:
            headers.append((b"retry-after", str(retry_after_s).encode("ascii")))
        await send({"type": "http.response.start", "status": exc.http_status, "headers": headers})
        await send({"type": "http.response.body", "body": body})
