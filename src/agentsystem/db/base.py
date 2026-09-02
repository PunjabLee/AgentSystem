"""SQLAlchemy 声明式基类与命名约定。

本模块只负责提供 ORM 基类，不含任何业务模型——模型在 models/ 下按领域拆分。
命名约定统一在此定义，使 Alembic 自动生成的约束名可预测、可 diff。
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# 显式命名约定。不配的话 PostgreSQL 会自动生成约束名，
# Alembic 在不同环境下 autogenerate 出的名字可能不一致，造成假 diff。
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """全部 ORM 模型的基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
