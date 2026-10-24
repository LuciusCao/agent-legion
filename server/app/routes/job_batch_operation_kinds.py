"""OpenAPI literal types for job batch operation handles."""

from typing import Literal

JobBatchOperationStatus = Literal[
    "pending", "running", "paused", "completed", "failed", "cancelled"
]

JobBatchOperationKindParam = Literal[
    "rerun",
    "rerun_by_failure",
    "delete",
    "run_to",
    "pause",
    "resume",
    "upgrade_workflow",
    "package",
    "clear_packed",
    "approve",
    "reject",
]
