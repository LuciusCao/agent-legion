"""Canonical job-batch kind vocabulary, mirrored by the DDL check."""

KIND_DELETE = "delete"

JOB_BATCH_OPERATION_KINDS: tuple[str, ...] = (
    "rerun",
    "rerun_by_failure",
    KIND_DELETE,
    "run_to",
    "pause",
    "resume",
    "upgrade_workflow",
    "package",
    "clear_packed",
    "approve",
    "reject",
)

KINDS_DEDUP_SELECTION = frozenset(
    {
        "rerun",
        "rerun_by_failure",
        KIND_DELETE,
        "upgrade_workflow",
        "package",
        "clear_packed",
        "approve",
        "reject",
    }
)
