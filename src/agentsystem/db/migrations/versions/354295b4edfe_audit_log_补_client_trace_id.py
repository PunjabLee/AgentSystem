"""audit_log 补 client_trace_id

§2.5.3 要求入站 X-Trace-Id「降级存入 audit_log.client_trace_id 作参考，
不作为权威」，但 P1.3.1 建表时漏了这一列。

⚠️ 本迁移改的是 audit_log，会被 P1.3.4 的事件触发器拦下。
   必须走 scripts/migrate_audit_schema.sh，不能直接 alembic upgrade。


Revision ID: 354295b4edfe
Revises: d25395f07f9f
Create Date: 2026-09-02 22:21:05.221011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "354295b4edfe"
down_revision: str | None = "d25395f07f9f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audit_log",
        sa.Column(
            "client_trace_id",
            sa.String(length=64),
            nullable=True,
            comment="入站 X-Trace-Id 的降级留存，仅作参考，永不作为权威 trace_id",
        ),
    )


def downgrade() -> None:
    op.drop_column("audit_log", "client_trace_id")
