"""P2.1.3 补 order_no 序列 —— WBS 三个并列项中漏掉的第三项

Revision ID: a7c1e2f40b31
Revises: eea304cd7f9c
Create Date: 2026-09-23 23:00:00

WBS P2.1.3 原文列了三件事：``confirm_token UNIQUE``、``UNIQUE(order_no, line_no)``、
``order_no 用 sequence``。前两项在 5b9c3e014e06 里做了，第三项漏了 —— 直到写
P2.3.4 的创建订单端点、需要一个新订单号时才发现。

为什么非用序列不可：替代方案是 ``SELECT max(order_no) + 1``，两个并发的下单请求
会读到同一个 max，其中一个撞主键失败。序列的 ``nextval`` 不参与事务、不会发重号。

代价是**号码会有空洞**（事务回滚时已取的号不归还）。订单号只要求唯一、单调，
不要求连续，这个代价可以接受。

种子数据用显式订单号插入，不推进序列 —— 由 ``fixtures/loader.resync_sequences``
在灌数后校准，与 ``sales_order_line_line_id_seq`` 同一套机制。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a7c1e2f40b31"
down_revision: str | None = "eea304cd7f9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建序列，并把起点放到现有数据之后。"""
    op.execute("CREATE SEQUENCE sales_order_no_seq AS bigint START WITH 1 MINVALUE 1")
    # 已有数据的库（开发机）必须立刻校准，否则第一次下单就撞种子数据的号。
    # 空库（CI）上 max 为 NULL，COALESCE 兜成 0，下一个号从 1 开始。
    op.execute(
        "SELECT setval('sales_order_no_seq', "
        "  COALESCE((SELECT max(CAST(split_part(order_no, '-', 3) AS bigint)) "
        "              FROM sales_order), 0) + 1, false)"
    )
    # 默认权限只覆盖 app_migrator 此后建的对象；显式补一次，不依赖默认权限
    # 是否在这个库上生效过 —— 缺它时报错是 permission denied for sequence，
    # 出现在第一次下单，而不是迁移时。
    op.execute("GRANT USAGE, SELECT ON SEQUENCE sales_order_no_seq TO app_rw")


def downgrade() -> None:
    """删序列。业务表不受影响，可安全回退。"""
    op.execute("DROP SEQUENCE IF EXISTS sales_order_no_seq")
