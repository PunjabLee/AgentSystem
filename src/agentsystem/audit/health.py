"""审计日志的自检 —— 找出只有 attempt 没有 outcome 的写操作。

两段式写入（attempt 行 → 业务事务 → outcome 行）的**全部理由**是：进程在
业务事务中途崩溃时，attempt 行已独立提交，因而「有人试过但结果不明」这件事
**可以被检测出来**。

但"可检测"是个性质，不是机制。P1 交付后复核发现：三处代码注释都以此作为
两段式的依据，而代码库里**没有任何检测手段** —— 性质无人背书。本模块补上。

实测（2026-09-09）：60 条写操作 0 条悬挂，机制本身工作正常。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text

from agentsystem.db.session import get_audit_sessionmaker

#: 宽限期。attempt 与 outcome 之间正常只隔一次业务事务（实测毫秒级），
#: 但给足余量以免把「正在执行的长事务」误报成悬挂。
DEFAULT_GRACE = timedelta(minutes=5)

_QUERY = text(
    "SELECT a.trace_id, a.user_id, a.action_type, a.tool_name, a.occurred_at "
    "  FROM audit_log a "
    " WHERE a.phase = 'attempt' "
    # 只查写操作（默认）。理由见 find_hanging_attempts 的 writes_only 参数说明。
    "   AND (a.action_type = 'write' OR NOT :writes_only) "
    # 🔴 CAST 与 timedelta 两者缺一不可，实测踩过两次：
    #    · CAST + 字符串 → asyncpg 按目标类型严格校验，拒绝 str（psycopg 会转，
    #      两个驱动在此不同）
    #    · 无 CAST + timedelta → 参数类型推断失败，`now() - $1` 被当成 interval，
    #      报 `operator does not exist: timestamp with time zone < interval`
    "   AND a.occurred_at < now() - CAST(:grace AS interval) "
    "   AND NOT EXISTS ( "
    "         SELECT 1 FROM audit_log o "
    "          WHERE o.trace_id = a.trace_id AND o.phase = 'outcome') "
    " ORDER BY a.occurred_at"
)


@dataclass(frozen=True)
class HangingAttempt:
    """一条有 attempt 无 outcome 的记录。"""

    trace_id: str
    user_id: str
    action_type: str
    tool_name: str | None
    occurred_at: object

    def describe(self) -> str:
        """给运维看的一行摘要。"""
        return (
            f"{self.occurred_at}  {self.trace_id}  "
            f"user={self.user_id}  {self.action_type}  tool={self.tool_name or '-'}"
        )


async def find_hanging_attempts(
    grace: timedelta = DEFAULT_GRACE, *, writes_only: bool = True
) -> list[HangingAttempt]:
    """列出超过宽限期仍无 outcome 的 attempt 行。

    走审计专用池：审计的自检不该和业务抢连接，这与两段式写入用独立池
    是同一个理由（共池在池满时会死锁，实测 0/5 成功）。

    Args:
        grace: 宽限期。小于它的 attempt 视为「可能仍在执行」，不报。
        writes_only: 默认只查写操作。两段式是**写路径**的机制 —— 装饰器仅在
            ``action_type == "write"`` 时写 attempt 行（``decorator.py``），
            故读操作的 attempt 行在真实流程中不存在。开发库里那些
            ``action_type='read'`` 的 attempt 是测试直接用 SQL 播的种子，
            且因审计表仅追加而**永久留存、删不掉** —— 默认不查它们，否则
            本工具在开发环境恒为红色，很快就没人看了。
            排查异常写入源时传 ``False`` 查全部。

    Returns:
        按发生时间升序的悬挂记录。空列表表示健康。
    """
    async with get_audit_sessionmaker()() as session:
        rows = (await session.execute(_QUERY, {"grace": grace, "writes_only": writes_only})).all()
    return [
        HangingAttempt(
            trace_id=r.trace_id,
            user_id=r.user_id,
            action_type=r.action_type,
            tool_name=r.tool_name,
            occurred_at=r.occurred_at,
        )
        for r in rows
    ]
