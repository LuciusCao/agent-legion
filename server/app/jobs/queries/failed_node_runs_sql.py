"""SQL builder for the latest-failed-run reads (#713, split out of
``failed_node_runs.py`` for the file-size budget)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

#: Keyset position (finished_at, node_run_id) of the last row of a page;
#: finished_at may be NULL (rows order ``finished_at desc`` = NULLS FIRST).
#: Cursor text codec: ``failed_run_cursor`` (kept out of the facade's import
#: graph — it reuses the job-list cursor parser, which imports the facade).
FailedRunCursor = tuple[datetime | None, int]


def latest_failed_runs_sql(
    workspace_id: str,
    *,
    category: str | None = None,
    detail: str | None = None,
    since: datetime | None = None,
    job_ids: Sequence[str] | None = None,
    node_key: str | None = None,
    before: FailedRunCursor | None = None,
) -> tuple[str, list[Any]]:
    """``from node_runs latest ... where ...`` over each (job, node)'s latest
    run that failed, plus its parameters; shared by the row and the job-id
    queries. The ``not exists`` probe is the "no newer run" half of the
    latest-run definition (equivalent to the retired ``row_number() = 1``)."""
    clauses = ["latest.status = 'failed'", "jobs.workspace_id = %s"]
    params: list[Any] = [workspace_id]
    # #211 Phase 3 (read-layer binding): the workflow_key predicate was
    # redundant — jobs.workspace_id (the join key above) already filters,
    # and the column equals it on every row (v62 binding). The parameter
    # stays signature-compatible; callers stop passing it.
    if job_ids:
        placeholders = ",".join("%s" for _ in job_ids)
        clauses.append(f"latest.job_id in ({placeholders})")
        params.extend(str(job_id) for job_id in job_ids)
    if node_key:
        clauses.append("latest.node_key = %s")
        params.append(node_key)
    if category:
        clauses.append("latest.failure_category = %s")
        params.append(category)
    if detail:
        clauses.append("latest.failure_detail = %s")
        params.append(detail)
    if since is not None:
        clauses.append("latest.finished_at >= %s")
        params.append(since)
    if before is not None:
        finished_at, run_id = before
        if finished_at is None:
            # NULL finished_at sorts first under ``desc``: after a NULL-row
            # cursor come the remaining NULL rows, then every non-NULL row.
            clauses.append("(latest.finished_at is not null or latest.id < %s)")
            params.append(run_id)
        else:
            clauses.append("(latest.finished_at, latest.id) < (%s, %s)")
            params.extend([finished_at, run_id])
    where = " and ".join(clauses)
    sql = f"""
        from node_runs latest
        join jobs on jobs.id = latest.job_id
        where {where}
          and not exists (
            select 1 from node_runs newer
            where newer.job_id = latest.job_id
              and newer.node_key = latest.node_key
              and newer.id > latest.id
          )
    """
    return sql, params
