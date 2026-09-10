"""模拟数据的生成与校验（P2.2.x）。"""

from agentsystem.fixtures.assertions import CheckResult, run_all_checks
from agentsystem.fixtures.generator import Dataset, generate
from agentsystem.fixtures.spec import SEED

__all__ = ["SEED", "CheckResult", "Dataset", "generate", "run_all_checks"]
