"""并发闸门与请求限流（P2.3.1 的「限流」一项）。

## 为什么限流在这个项目里不是装饰性的

业务连接池只有 5 条连接且 ``max_overflow=0``（``db/session.py``）。这意味着
**第 6 个同时触库的请求必然排队**，排多久由 ``pool_timeout`` 决定。

排队的坏处不在于慢，在于**它长得像别的东西**：本项目每个请求背后都可能有一次
LLM 调用（秒级），一个在连接池上静默等 30 秒的请求，从外部观察与「大模型今天
有点慢」无法区分。等到 P5 做性能评测时，这类等待会被算进模型延迟里。

所以闸门的取值必须与池大小一起定，不能各定各的。

## 实现取舍

进程内计数，不用 Redis —— 单进程 PoC，引入 Redis 只是把状态挪个地方，
并不会让这个数字更准。多进程部署时本模块需要重写，已记在 OPEN-ITEMS。
"""

import asyncio
from dataclasses import dataclass

from agentsystem.errors import AppError


class TooManyRequests(AppError):
    """并发超出闸门。

    ``retryable=True`` —— 这是**容量**问题不是业务拒绝，稍后重试确实可能成功。
    这与 :class:`~agentsystem.errors.BusinessRejection` 的区别是 P4 降级判定的
    分界线：库存不足重试一万次也没用，而挤满了的闸门过一会儿就空了。
    """

    code = "SYS_BUSY"
    retryable = True
    http_status = 429


@dataclass(frozen=True, slots=True)
class GateDecision:
    """闸门放行与否，以及给调用方的解释。"""

    allowed: bool
    #: 建议的重试等待秒数；放行时无意义。写进 ``Retry-After`` 头。
    retry_after_s: int = 1


class ConcurrencyGate:
    """限制**同时在途**的请求数。

    刻意不做「每分钟 N 次」的速率窗口：本项目的稀缺资源是连接池里那 5 条
    连接，它是**并发**约束而非**频次**约束。一分钟 300 次但从不并发，池子
    毫无压力；一分钟 6 次却同时打进来，池子就满了。按频次限流会放过后者。
    """

    def __init__(self, limit: int) -> None:
        """建闸门。

        Args:
            limit: 允许同时在途的请求数。
        """
        self._limit = limit
        self._in_flight = 0
        # asyncio.Lock 而非裸整数自增：单线程事件循环里 ``+= 1`` 本身是原子的，
        # 但「读计数 → 判断 → 自增」这三步之间有 await 点时就不是了。这里虽然
        # 当前实现中途没有 await，加锁是为了防止日后有人在中间插一句 await
        # 而不自知 —— 那种 bug 只在高并发下偶发，极难复现。
        self._lock = asyncio.Lock()

    @property
    def in_flight(self) -> int:
        """当前在途请求数。仅供测试与 ``/healthz`` 观测。"""
        return self._in_flight

    async def acquire(self) -> GateDecision:
        """尝试占一个名额。

        **立刻返回，不排队** —— 排队正是本模块要消灭的东西。调用方拿到
        ``allowed=False`` 应当立即回 429，让客户端自己决定什么时候重试。

        Returns:
            放行与否。
        """
        async with self._lock:
            if self._in_flight >= self._limit:
                return GateDecision(allowed=False)
            self._in_flight += 1
            return GateDecision(allowed=True)

    async def release(self) -> None:
        """归还名额。**必须在 finally 中调用** —— 漏掉一次，闸门就永久小一格。"""
        async with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
