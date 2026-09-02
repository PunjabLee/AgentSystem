"""审计写入（宪法第一条的落地物）。"""

from agentsystem.audit.decorator import WriteOutcome, audited
from agentsystem.audit.writer import (
    metrics_from_llm_result,
    write_attempt,
    write_outcome,
)

__all__ = [
    "WriteOutcome",
    "audited",
    "metrics_from_llm_result",
    "write_attempt",
    "write_outcome",
]
