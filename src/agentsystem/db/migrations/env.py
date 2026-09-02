"""Alembic 迁移环境。

两处不能省的配置：
  1. 连接串从 settings 读取而非 alembic.ini —— 避免明文口令入库（宪法第七条）。
  2. include_object 过滤 checkpointer 的表 —— 见下方 should_include 的说明。
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from agentsystem.db.base import Base
from agentsystem.db.models import *  # noqa: F401,F403  —— 触发模型注册，autogenerate 依赖
from agentsystem.settings import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 用 migrator 角色。若用运行时账号跑迁移，它会成为表属主，
# 而属主可 DISABLE TRIGGER，P1.3.4 的防篡改层整层失效。
config.set_main_option("sqlalchemy.url", get_settings().dsn("migrator", driver="sync"))

target_metadata = Base.metadata

#: 不由本项目 ORM 管理、但存在于同一 schema 的表前缀。
#:
#: LangGraph 的 AsyncPostgresSaver.setup() 会自建 checkpoints / checkpoint_blobs /
#: checkpoint_writes / checkpoint_migrations 四张表。它们不在 Base.metadata 里，
#: autogenerate 会认为「数据库里有、模型里没有」从而生成 drop_table —— 一次
#: alembic upgrade 就会抹掉全部会话检查点。
_UNMANAGED_TABLE_PREFIXES = ("checkpoint",)


def should_include(
    object_: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object,
) -> bool:
    """决定某个数据库对象是否纳入 autogenerate 比对。

    签名由 Alembic 规定，五个参数均以位置传入，不可改为关键字形式。

    Args:
        object_: SQLAlchemy 的 schema 对象。
        name: 对象名。
        type_: 对象类型，``table`` / ``index`` / ``column`` 等。
        reflected: True 表示该对象来自数据库反射，而非 ORM 模型。
        compare_to: 比对目标，无对应物时为 None。

    Returns:
        True 表示纳入比对；False 表示 Alembic 完全忽略它。
    """
    if type_ == "table" and name is not None:
        return not name.startswith(_UNMANAGED_TABLE_PREFIXES)
    return True


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连接数据库。"""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=should_include,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=should_include,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
