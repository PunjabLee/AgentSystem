"""把生成的数据写进库（P2.2.2 的落地部分）。

插入顺序取自 :class:`~agentsystem.fixtures.generator.Dataset` 的**字段声明顺序**，
那就是拓扑序。不建外键（设计文档 §4.0）之后顺序错了不会报错，只会留下孤儿行 ——
所以顺序不能靠调用点自觉，要么编码进数据结构，要么被断言查出来。这里两者都做。
"""

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agentsystem.fixtures.generator import Dataset

#: 表名 → Dataset 字段名。顺序即插入顺序，与 Dataset 的字段声明一致。
_TABLES: list[tuple[str, str]] = [
    ("product", "products"),
    ("color", "colors"),
    ("production_line", "lines"),
    ("sales_order", "orders"),
    ("sales_order_line", "order_lines"),
    ("inventory_batch", "batches"),
    ("production_plan", "plans"),
]

#: 需要序列化成 JSON 文本再交给 CAST 的列。asyncpg 不接受 dict 直接绑 JSONB。
_JSONB_COLUMNS = {"attrs"}


async def truncate_all(db: AsyncSession) -> None:
    """清空业务表。

    按插入顺序的**逆序**删 —— 虽然没有外键拦着，但保持逆序是为了让「哪天加回
    外键」不至于要重写本函数。RESTART IDENTITY 把自增序列一并归零，否则重复
    灌数据时 line_id 会持续增长，而生成器里的 related_order_line 是按 1 起算的。

    ⚠️ 只清业务表。``audit_log`` 不在其列 —— 它是仅追加的，TRUNCATE 会被
    防篡改触发器拒绝（宪法第一条），这是刻意的。
    """
    names = ", ".join(t for t, _ in reversed(_TABLES))
    await db.execute(text(f"TRUNCATE {names} RESTART IDENTITY"))


async def load(db: AsyncSession, ds: Dataset) -> dict[str, int]:
    """按拓扑序把数据集写进库。

    Args:
        db: 业务库连接（需 INSERT 权限）。
        ds: 生成器的产出。

    Returns:
        表名 → 写入行数。
    """
    written: dict[str, int] = {}
    for table, attr in _TABLES:
        rows: list[dict] = getattr(ds, attr)
        if not rows:
            written[table] = 0
            continue

        cols = list(rows[0])
        # JSONB 列走 CAST：asyncpg 不接受 dict 直接绑 JSONB，
        # 与 audit writer 里 request_payload 的处理方式一致。
        placeholders = ", ".join(
            f"CAST(:{c} AS jsonb)" if c in _JSONB_COLUMNS else f":{c}" for c in cols
        )
        sql = text(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})")

        payload = [
            {
                k: (json.dumps(v, ensure_ascii=False) if k in _JSONB_COLUMNS else v)
                for k, v in row.items()
            }
            for row in rows
        ]
        await db.execute(sql, payload)
        written[table] = len(rows)
    return written
