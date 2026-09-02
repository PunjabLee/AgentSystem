"""ORM 模型集合。

Alembic 的 autogenerate 依赖本模块把全部模型导入到 Base.metadata，
新增模型必须在此 import，否则迁移不会包含它。
"""

from agentsystem.db.models.audit import AuditLog
from agentsystem.db.models.intent import WriteIntent

__all__ = ["AuditLog", "WriteIntent"]
