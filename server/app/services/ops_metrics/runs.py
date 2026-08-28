"""Recent-hour Agent run stats for the ops-metrics summary.

Split out of the summary for the file-size budget. Run stats
cover Agent runs only — a run counts when an ``agent_execution_requests``
row references it (same attribution the sampler uses); Host-local handler
(code) nodes never have one and are excluded. The run table has no worker
attribution, so worker-scoped summaries still read global run stats;
workspace scope joins ``jobs`` on the run's job. Duration percentiles cover
``completed`` runs only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from server.app.db.connection import DatabaseConnection


def query_recent_hour_runs(
    conn: DatabaseConnection, cutoff: datetime, workspace_id: str | None = None
) -> dict[str, Any]:
    """Aggregate completed/failed counts and duration percentiles for 1h."""
    workspace_clause = ""
    params: tuple[Any, ...] = (cutoff,)
    if workspace_id is not None:
        workspace_clause = " and exists (select 1 from jobs j where j.id = node_runs.job_id and j.workspace_id = %s)"
        params = (cutoff, workspace_id)
    runs = conn.execute(
        f"""
        select count(*) filter (where status = 'completed') as completed,
               count(*) filter (where status = 'failed') as failed,
               percentile_cont(0.5) within group (
                 order by extract(epoch from finished_at - started_at)
               ) filter (where status = 'completed') as p50,
               percentile_cont(0.95) within group (
                 order by extract(epoch from finished_at - started_at)
               ) filter (where status = 'completed') as p95
        from node_runs
        where status in ('completed', 'failed') and finished_at >= %s
          and exists (
            select 1 from agent_execution_requests r where r.node_run_id = node_runs.id
          ){workspace_clause}
        """,
        params,
    ).fetchone()
    assert runs is not None  # aggregate queries always return one row
    return {
        "completed": int(runs["completed"]),
        "failed": int(runs["failed"]),
        "duration_p50_seconds": (float(runs["p50"]) if runs["p50"] is not None else None),
        "duration_p95_seconds": (float(runs["p95"]) if runs["p95"] is not None else None),
    }
