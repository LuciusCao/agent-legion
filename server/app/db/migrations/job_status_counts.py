"""Data migration applied alongside the idempotent DDL replay (v36)."""

from __future__ import annotations

from typing import Any

# Workspace job status counts (schema v36): count_jobs_by_status feeds the
# workspace event aggregator flush (every 0.5s per dirty workspace), the job
# list snapshot, intake and deletion broadcasts. As a group-by over the whole
# workspace slice of jobs it is O(workspace jobs) per call and was measured at
# 0.3~1.1s under load at 130k rows. The counter table is maintained
# transactionally by row triggers on jobs (DB-JOB-STATUS-COUNTS-001), turning
# the read into a PK lookup of a handful of rows regardless of table size.
# The backfill below rebuilds the table from jobs; on-conflict replace makes
# the whole migration idempotent and self-healing on replay.
_BACKFILL_SQL = """
insert into workspace_job_status_counts(workspace_id, status, cnt)
select workspace_id, status, count(*) from jobs group by 1, 2
on conflict (workspace_id, status) do update set cnt = excluded.cnt
"""


def migrate_workspace_job_status_counts(conn: Any) -> None:
    """Backfill workspace_job_status_counts from jobs (v36); idempotent."""
    conn.execute(_BACKFILL_SQL)
