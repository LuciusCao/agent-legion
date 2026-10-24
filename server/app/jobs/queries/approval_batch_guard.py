"""In-transaction batch-only approval guard."""

from typing import Any


def guard_batch_single_awaiting(
    conn: Any, decision: dict[str, Any], conflict_type: type[Exception]
) -> None:
    if decision.get("batch_id") is None:
        return
    row = conn.execute(
        "select count(*) as cnt from job_nodes where job_id=%s and status='awaiting_approval'",
        (decision["job_id"],),
    ).fetchone()
    if int(row["cnt"]) != 1:
        raise conflict_type("Batch approval requires exactly one awaiting gate")
