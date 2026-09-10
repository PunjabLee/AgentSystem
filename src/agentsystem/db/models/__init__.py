"""ORM 模型集合。

Alembic 的 autogenerate 依赖本模块把全部模型导入到 Base.metadata，
新增模型必须在此 import，否则迁移不会包含它。
"""

from agentsystem.db.models.audit import AuditLog
from agentsystem.db.models.intent import WriteIntent
from agentsystem.db.models.inventory import GRADES, QC_STATUSES, InventoryBatch
from agentsystem.db.models.master import BU_CODES, Color, Product, ProductionLine
from agentsystem.db.models.order import (
    BATCH_POLICIES,
    ORDER_STATUSES,
    SalesOrder,
    SalesOrderLine,
)
from agentsystem.db.models.plan import PLAN_STATUSES, ProductionPlan

__all__ = [
    "AuditLog",
    "BATCH_POLICIES",
    "BU_CODES",
    "Color",
    "GRADES",
    "InventoryBatch",
    "ORDER_STATUSES",
    "PLAN_STATUSES",
    "Product",
    "ProductionLine",
    "ProductionPlan",
    "QC_STATUSES",
    "SalesOrder",
    "SalesOrderLine",
    "WriteIntent",
]
