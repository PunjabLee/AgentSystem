"""审计写入（宪法第一条的落地物）。"""

from agentsystem.audit.decorator import WriteOutcome, audited
from agentsystem.audit.writer import write_attempt, write_outcome

__all__ = ["WriteOutcome", "audited", "write_attempt", "write_outcome"]
