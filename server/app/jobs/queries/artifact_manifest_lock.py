"""Per-job manifest-write lock shared by legacy registration and recovery (#1019).

Legacy no-lease registration takes the exclusive lock for its whole write
transaction. Recovery try-acquires it before its final projection read, so an
active writer forces stale_plan while a later writer waits until after COMMIT.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

_PREFIX = "artifact-manifest:"


def lock_artifact_manifest_jobs(conn: Any, job_ids: Iterable[str]) -> None:
    """Lock distinct job ids in global order before any manifest-row write."""
    for job_id in sorted({str(value) for value in job_ids}):
        conn.execute(
            "select pg_advisory_xact_lock(hashtext(%s))",
            (_PREFIX + job_id,),
        )


def try_artifact_manifest_lock(conn: Any, job_id: str) -> bool:
    """Try the same lock without waiting; False means an active writer holds it."""
    row = conn.execute(
        "select pg_try_advisory_xact_lock(hashtext(%s)) as locked",
        (_PREFIX + str(job_id),),
    ).fetchone()
    return bool(row and row["locked"])
