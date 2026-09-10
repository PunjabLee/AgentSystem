"""灌模拟数据并跑断言校验（P2.2.2 + P2.2.3）。

    uv run python scripts/seed.py            # 清空重灌 + 校验
    uv run python scripts/seed.py --check    # 只校验，不动数据

**生成完必须校验。** P0-2 造数据时两维完全相关导致四维过滤返回 0 行 ——
生成器当时"跑通了"，问题到用例跑不出结果才暴露。
"""

import argparse
import asyncio
import os
import pathlib
import sys

for _line in pathlib.Path(".env").read_text(encoding="utf-8").splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from agentsystem.fixtures import generate, run_all_checks  # noqa: E402
from agentsystem.fixtures.loader import load, truncate_all  # noqa: E402
from agentsystem.settings import get_settings  # noqa: E402


def _admin_sessionmaker() -> async_sessionmaker:
    """以 app_migrator 连库。

    🔴 灌数据走**管理角色**而非运行时角色，不是图省事：``TRUNCATE`` 是独立于
    SELECT/INSERT/UPDATE/DELETE 的权限，``ALTER DEFAULT PRIVILEGES`` 没给
    ``app_rw``。第一次跑本脚本正是撞在这上面 —— 而正确的处置是让灌数据走管理
    角色，**不是给运行时角色补上 TRUNCATE**：运行时账号能清空业务表，本身就是
    个不该存在的能力。
    """
    engine = create_async_engine(
        get_settings().dsn("migrator", driver="asyncpg"), pool_size=2, max_overflow=0
    )
    return async_sessionmaker(engine, expire_on_commit=False)


async def main() -> int:
    """灌数据（可选）并跑全部断言。"""
    parser = argparse.ArgumentParser(description="灌模拟数据并校验")
    parser.add_argument("--check", action="store_true", help="只校验，不重灌")
    args = parser.parse_args()

    async with _admin_sessionmaker()() as db:
        if not args.check:
            print("═══ 清空并重灌 ═══")
            await truncate_all(db)
            written = await load(db, generate())
            await db.commit()
            for t, n in written.items():
                print(f"  {t:20} {n:5} 行")

        print("\n═══ 断言校验 ═══")
        results = await run_all_checks(db)
        for r in results:
            print(f"  {r.describe()}")

        failed = [r for r in results if not r.passed]
        print(f"\n  {len(results) - len(failed)}/{len(results)} 通过")
        if failed:
            print("\n  🔴 未通过的检查：")
            for r in failed:
                print(f"     {r.name} —— {r.detail}")
            print("\n  数据不可用于评测。均匀随机产不出关键形态，须在生成器里显式构造。")
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
