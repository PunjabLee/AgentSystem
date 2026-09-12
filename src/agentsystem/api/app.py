"""FastAPI 应用装配（P2.3.1）。

## 这一层只做三件事

装中间件、装异常处理器、装路由。**不含任何业务逻辑** —— 业务在端点模块里，
端点通过 ``db/scope.py`` 访问数据。这个分界让「绕过 Gateway」变成一件显眼的事
（宪法第五条）。

## 异常处理器为什么必须兜底到 Exception

未捕获异常若走 Starlette 默认路径，返回的是纯文本 ``Internal Server Error``，
没有 ``retryable`` 字段。P4 的降级判定读不到该字段时只能猜，而它猜错的代价是
「拿 RPA 去重试一件业务上不该成立的事」。所以宁可多写一个 handler，
也不让任何一条错误路径逃出包络格式。
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentsystem.db.session import dispose_engines
from agentsystem.errors import AppError, SystemError_, ValidationError
from agentsystem.gateway.context import current_context
from agentsystem.gateway.identity import new_conversation_id
from agentsystem.gateway.middleware import GatewayMiddleware
from agentsystem.gateway.ratelimit import ConcurrencyGate

logger = logging.getLogger(__name__)

#: 同时在途请求的上限。**刻意远大于连接池的 5 条连接** ——
#: 第一版按「与池大小配套」取 5，实测证明那是错的：300 个真实业务查询并发打进
#: 5 条连接的池子，0.28 秒全部完成（最慢 235ms）；闸门取 5 会拒掉其中 295 个
#: 池子本来吃得下的请求，把优雅排队变成大规模拒绝。
#:
#: 闸门的职责因此不是「镜像池大小」，而是**给失控流量封顶**：一个写错的重试
#: 循环不该能压进上万个在途请求。慢查询造成的拥塞由 ``_POOL_TIMEOUT_S`` 兜底，
#: 两条路径返回同一个 ``SYS_BUSY`` / 429，调用方不必区分是哪一层挡的。
#:
#: 取值与全部实测数据见 ``docs/measurements/pool-under-load.md``。
DEFAULT_CONCURRENCY_LIMIT = 64


def _trace_id() -> str:
    """取当前请求的 trace_id；上下文缺失时给一个占位符。

    异常处理器是最后一道，**它自己绝不能再抛异常** —— 那会把一个业务错误
    变成一个没有响应体的 500，排障时连错在哪都看不见。
    """
    try:
        return current_context().trace_id
    except LookupError:
        return "-"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：启动什么都不做，关停时释放连接池。

    不在启动时预热连接：预热会让「数据库没起来」这件事在启动时就炸，
    而 PoC 演示现场更需要的是「服务起得来，第一个请求告诉你库连不上」。
    """
    yield
    await dispose_engines()


def create_app(*, concurrency_limit: int = DEFAULT_CONCURRENCY_LIMIT) -> FastAPI:
    """装配应用。

    Args:
        concurrency_limit: 并发闸门上限。测试用小值制造拥塞。

    Returns:
        已装好中间件与异常处理器的 FastAPI 实例。
    """
    app = FastAPI(
        title="AgentSystem PoC API",
        version="0.2.0",
        description="订单全生命周期的业务查询与写操作接口。",
        lifespan=lifespan,
    )

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        """业务异常 → 统一包络。"""
        return JSONResponse(
            status_code=exc.http_status,
            content=exc.to_envelope(_trace_id()),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        """入参校验失败 → 包络里的 VAL_INVALID。

        把 pydantic 的报错摘成一句中文：原始报错是给开发者的 JSON 结构，
        而这条消息会进模型上下文 —— 模型据此改参数重试，看得懂才改得对。
        """
        parts = [f"{'.'.join(str(x) for x in e['loc'][1:])}: {e['msg']}" for e in exc.errors()[:3]]
        wrapped = ValidationError("入参不合法 —— " + "；".join(parts))
        return JSONResponse(
            status_code=wrapped.http_status, content=wrapped.to_envelope(_trace_id())
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """框架自身的 HTTP 异常（404、405 等）→ 统一包络。

        没有这个处理器，一个走错路径的调用会拿到 Starlette 默认的
        ``{"detail": "Not Found"}`` —— 里面**没有 ``retryable``**。
        模型（以及 P4 的降级判定）读不到该字段就只能猜，而 404 是它最容易
        撞上的错误：工具名写错、路径拼错都落在这里。

        4xx 一律不可重试，5xx 交给可重试的系统错误 —— 路由不存在这件事
        重试多少次都不会变。
        """
        wrapped: AppError
        if exc.status_code >= 500:
            wrapped = SystemError_(str(exc.detail))
        else:
            wrapped = ValidationError(f"请求无法处理（HTTP {exc.status_code}）：{exc.detail}")
            wrapped.http_status = exc.status_code  # 保留原状态码的 HTTP 语义
        return JSONResponse(
            status_code=wrapped.http_status, content=wrapped.to_envelope(_trace_id())
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        """兜底：任何未预期异常 → SYS_INTERNAL（可重试）。

        🔴 细节不外泄。栈里可能有 DSN、令牌片段，而这个响应体会被原样送进
        模型上下文，再由模型复述给用户 —— 那就是一条从日志到聊天窗的泄漏通道。
        """
        logger.exception("未处理异常 path=%s trace_id=%s", request.url.path, _trace_id())
        wrapped = SystemError_("服务内部错误，请携带 trace_id 联系管理员")
        return JSONResponse(
            status_code=wrapped.http_status, content=wrapped.to_envelope(_trace_id())
        )

    @app.get("/healthz", summary="健康检查", tags=["运维"])
    async def healthz() -> dict[str, Any]:
        """存活探针。**不查库** —— 它要回答的是「进程还在吗」。

        库的可用性由 ``make audit-health`` 与业务端点自身的错误反映；
        把库检查塞进存活探针，会让一次库抖动引发容器重启，把小问题放大。
        """
        return {"status": "ok"}

    @app.post("/v1/sessions", summary="领取会话标识", tags=["运维"])
    async def issue_session() -> dict[str, str]:
        """签发一个 ``conversation_id``，后续请求用 ``X-Conversation-Id`` 回传。

        会话标识必须由服务端签发（P1 详细设计 §2.5.3）：客户端可自选取值时，
        两段逻辑上无关的会话会派生出同一个 ``session_id``，于是会话 A 里创建的
        待确认写操作，会在会话 B 里被当成本会话的项 —— 用户可能确认了
        他没打算确认的那一单。
        """
        return {"conversation_id": new_conversation_id()}

    # 中间件最后装：它要包住上面所有东西，包括异常处理器产生的响应
    # （trace_id 响应头对错误响应同样要有）。
    app.add_middleware(GatewayMiddleware, gate=ConcurrencyGate(concurrency_limit))
    return app
