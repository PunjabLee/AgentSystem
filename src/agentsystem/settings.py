"""全局配置。

只负责从环境变量读取并校验，不含任何业务逻辑。
宪法第七条：机密只走环境变量，本模块是唯一的读取入口。
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """从 .env 与环境变量加载的配置。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    pg_host: str = "127.0.0.1"
    pg_port: int = 5432
    pg_database: str = "agentsystem"

    # 三个数据库角色。分离不是洁癖——迁移账号是表属主，若与运行时账号
    # 合一，属主可 DISABLE TRIGGER，防篡改层整层失效（P1 详细设计 §3.4）。
    pg_migrator_user: str = "app_migrator"
    pg_migrator_password: str = Field(default="")
    pg_app_user: str = "app_rw"
    pg_app_password: str = Field(default="")
    pg_ro_user: str = "app_ro"
    pg_ro_password: str = Field(default="")

    # session_id 的 HMAC 派生密钥（P1 详细设计 §2.5.3）。泄漏即可伪造
    # 任意用户的 session_id，进而消费他人的 write_intent —— 与数据库口令同级。
    session_signing_key: str = Field(default="")

    def dsn(self, role: str = "app", *, driver: str = "asyncpg") -> str:
        """构造数据库连接串。

        Args:
            role: ``migrator`` | ``app`` | ``ro``，决定用哪套凭据。
            driver: ``asyncpg``（业务侧）、``psycopg``（checkpointer）
                或 ``sync``（Alembic，走同步 psycopg）。

        Returns:
            SQLAlchemy 格式的连接串。
        """
        user, pwd = {
            "migrator": (self.pg_migrator_user, self.pg_migrator_password),
            "app": (self.pg_app_user, self.pg_app_password),
            "ro": (self.pg_ro_user, self.pg_ro_password),
        }[role]
        scheme = {
            "asyncpg": "postgresql+asyncpg",
            "psycopg": "postgresql+psycopg",
            "sync": "postgresql+psycopg",
        }[driver]
        return f"{scheme}://{user}:{pwd}@{self.pg_host}:{self.pg_port}/{self.pg_database}"


@lru_cache
def get_settings() -> Settings:
    """返回进程内单例配置。"""
    return Settings()
