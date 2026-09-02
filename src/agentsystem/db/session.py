"""异步数据库会话工厂（宪法第四条：全链路 async）。

两个独立引擎，不是冗余：

* ``app`` 引擎走 app_rw，承载全部业务读写。
* ``audit`` 引擎同样走 app_rw 但**连接池独立**——两段式审计的 attempt 行
  必须在业务事务之外提交（P1 详细设计 §3.2）。若共用池，业务事务耗尽连接
  时审计写入会排队等待，最终"操作发生了但没有 attempt 记录"，这恰是宪法
  第一条要防的情形。

驱动选择：业务侧一律 asyncpg。psycopg3 只用于 langgraph-checkpoint-postgres，
那是它的硬依赖，不在本模块管辖内。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agentsystem.settings import get_settings

#: 业务池大小。单人 PoC 且 max_connections 需与 Dify 的 14 个容器共享，
#: 故刻意保守；P1.1.6 会核算总量。
_APP_POOL_SIZE = 5
#: 审计池只承载短事务（一行 INSERT）。P1 详细设计 §3.2 定为 3，与之对齐。
_AUDIT_POOL_SIZE = 3


def _make_engine(pool_size: int) -> AsyncEngine:
    """按池大小建异步引擎。"""
    return create_async_engine(
        get_settings().dsn("app", driver="asyncpg"),
        pool_size=pool_size,
        max_overflow=0,  # 不允许溢出：宁可排队暴露容量问题，也不悄悄突破核算
        pool_pre_ping=True,  # Dify 重启会掐断连接，预检避免首个请求必失败
    )


@lru_cache
def get_app_engine() -> AsyncEngine:
    """业务引擎（进程内单例）。"""
    return _make_engine(_APP_POOL_SIZE)


@lru_cache
def get_audit_engine() -> AsyncEngine:
    """审计引擎（进程内单例，连接池与业务隔离）。"""
    return _make_engine(_AUDIT_POOL_SIZE)


@lru_cache
def get_app_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """业务会话工厂。"""
    return async_sessionmaker(get_app_engine(), expire_on_commit=False)


@lru_cache
def get_audit_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """审计会话工厂。"""
    return async_sessionmaker(get_audit_engine(), expire_on_commit=False)


@asynccontextmanager
async def app_session() -> AsyncIterator[AsyncSession]:
    """业务会话上下文：正常提交，异常回滚。"""
    async with get_app_sessionmaker()() as session, session.begin():
        yield session


async def dispose_engines() -> None:
    """关闭全部连接池。仅供应用关停与测试收尾调用。"""
    for factory in (get_app_engine, get_audit_engine):
        if factory.cache_info().currsize:
            await factory().dispose()
    for f in (get_app_engine, get_audit_engine, get_app_sessionmaker, get_audit_sessionmaker):
        f.cache_clear()
