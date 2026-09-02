"""统一审计装饰器（P1 详细设计 §3.3，宪法第一条的落地物）。

``trace_id`` / ``session_id`` / ``user_id`` 一律从 contextvar 取，
**不从函数参数取** —— 否则每个业务函数都要多三个参数，且调用方可伪造。
CI 有机械检查（§6）：审计写入路径不得出现从请求体取 user_id 的代码。

读写两条路径的审计**刻意不对称**：

* 写操作出两行（attempt + outcome）。两段式存在的全部理由是「业务回滚时
  审计不能一起消失」，而这只对有副作用的操作成立。
* 读操作出一行（outcome）。读失败没有副作用，事后一行足以回答「谁查了什么」；
  为它多付一次独立提交，是拿真实的延迟换一个不存在的风险。
"""

import functools
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, ParamSpec, TypeVar

from agentsystem.audit.writer import write_attempt, write_outcome
from agentsystem.errors import AppError, ConfirmationRequired
from agentsystem.gateway.context import current_context

P = ParamSpec("P")
R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class WriteOutcome:
    """业务函数向审计层回报变更详情的载体。

    业务函数返回它，装饰器把 ``value`` 透传给调用方、把其余字段落进
    outcome 行。不返回它也可以 —— 那样 outcome 行没有前后值，适用于
    读操作与无明确目标行的写操作。
    """

    value: Any
    target_id: str | None = None
    before_value: dict | None = None
    after_value: dict | None = None


def audited(
    action_type: Literal["read", "write"],
    *,
    source: str = "langgraph",
    target_table: str | None = None,
    tool_name: str | None = None,
    require_confirm: bool = False,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """给业务函数套上审计。

    Args:
        action_type: ``read`` 或 ``write``，决定是否走两段式。
        source: 调用来源，``langgraph`` | ``dify`` | ``rpa``。
        target_table: 目标表名，落进审计行便于按对象检索。
        tool_name: 工具名，缺省取被装饰函数的名字。
        require_confirm: 写操作是否需要二次确认。置 True 后，调用时缺
            ``confirm_token`` 关键字参数直接抛 ``ConfirmationRequired``
            —— 这是宪法第一条的**唯一机械保障**，必须有对应负测试。

    Returns:
        装饰器。

    Raises:
        ValueError: ``require_confirm`` 用在读操作上 —— 读不需要确认，
            这样写必是笔误，早失败好过在生产上悄悄放行。
    """
    if require_confirm and action_type != "write":
        raise ValueError("require_confirm 只对 action_type='write' 有意义")

    def decorate(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        name = tool_name or fn.__name__

        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            ctx = current_context()
            token = kwargs.get("confirm_token")

            if require_confirm and not token:
                # 在写 attempt 行之前就拒 —— 一次连令牌都没带的调用不是
                # 「尝试写入」，是调用方用错了 API。
                raise ConfirmationRequired(
                    "该操作需要二次确认，请先获取确认令牌", code="CONF_TOKEN_MISSING"
                )

            confirm_token = token if isinstance(token, str) else None
            started = time.perf_counter()

            if action_type == "write":
                await write_attempt(
                    ctx,
                    action_type=action_type,
                    source=source,
                    tool_name=name,
                    target_table=target_table,
                    confirm_token=confirm_token,
                )

            try:
                result = await fn(*args, **kwargs)
            except AppError as exc:
                await write_outcome(
                    ctx,
                    action_type=action_type,
                    source=source,
                    # 业务拒绝与用户取消不是「失败」。P4 的降级判定读这一列，
                    # 把「库存不足」记成 failed 会让人误以为系统出过故障。
                    status="cancelled" if exc.code.startswith("CONF_") else "failed",
                    tool_name=name,
                    target_table=target_table,
                    confirm_token=confirm_token,
                    error_code=exc.code,
                    latency_ms=_elapsed_ms(started),
                )
                raise
            except Exception as exc:
                await write_outcome(
                    ctx,
                    action_type=action_type,
                    source=source,
                    status="failed",
                    tool_name=name,
                    target_table=target_table,
                    confirm_token=confirm_token,
                    error_code=type(exc).__name__,
                    latency_ms=_elapsed_ms(started),
                )
                raise

            detail = result if isinstance(result, WriteOutcome) else None
            await write_outcome(
                ctx,
                action_type=action_type,
                source=source,
                status="success",
                tool_name=name,
                target_table=target_table,
                target_id=detail.target_id if detail else None,
                before_value=detail.before_value if detail else None,
                after_value=detail.after_value if detail else None,
                confirm_token=confirm_token,
                latency_ms=_elapsed_ms(started),
            )
            return detail.value if detail else result  # type: ignore[return-value]

        return wrapper

    return decorate


def _elapsed_ms(started: float) -> int:
    """从 ``perf_counter`` 起点算出耗时毫秒。"""
    return int((time.perf_counter() - started) * 1000)
