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


#: 生成器显式赋值主键的表 → (主键列, 序列名)。
#: 只有这些需要事后校准序列 —— 让数据库自增的表不在其列。
_EXPLICIT_PK_SEQUENCES: list[tuple[str, str, str]] = [
    # line_id 必须显式赋值：production_plan.related_order_line 要引用它，
    # 而两表是分开插入的，无法先拿到自增值。
    ("sales_order_line", "line_id", "sales_order_line_line_id_seq"),
]


async def resync_sequences(db: AsyncSession) -> dict[str, int]:
    """把显式赋过值的主键序列校准到当前最大值。

    🔴 **显式插入主键不会推进序列。** 灌完 203 行订单行后序列仍停在个位数，
    下一次自增插入直接撞主键 —— 而那时的报错是 `duplicate key`，指向的是
    "有人插了重复数据"，与真正的原因（序列没跟上）差得很远。

    实测踩到：灌完数据后跑测试，一条本该报唯一约束冲突的用例改报了主键冲突。
    P2.3.4 的写端点插订单行时会撞上同一件事。

    Returns:
        序列名 → 校准后的值。
    """
    out: dict[str, int] = {}
    for table, pk, seq in _EXPLICIT_PK_SEQUENCES:
        # setval(..., false) 让下一个 nextval 返回该值本身；取 max+1 使新行
        # 落在已有数据之后。COALESCE 兜住空表。
        cur_max = f"COALESCE((SELECT max({pk}) FROM {table}), 0)"
        sql = text(f"SELECT setval('{seq}', {cur_max} + 1, false)")
        out[seq] = int((await db.execute(sql)).scalar_one())
    return out


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

    # 必须在全部插入之后 —— 序列校准的是「已灌完的最大值」。
    await resync_sequences(db)
    return written
