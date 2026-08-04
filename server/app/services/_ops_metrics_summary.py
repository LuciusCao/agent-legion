"""Window-independent summary for the Host operations metrics panel.

Split out of ``ops_metrics.py`` to respect that module's size budget. The
summary feeds the monitoring cards, which must stay stable while the chart
window switches: token and gauge values always come from minute-resolution
samples (``bucket_start >= now - 1h`` for tokens, latest minute row for
gauges), and run stats are aggregated on demand from ``node_runs``. Run stats
cover Agent runs only — a run counts when an ``agent_execution_requests``
row references it (same attribution the sampler uses); Host-local handler
(code) nodes never have one and are excluded. The run table has no worker
attribution, so run stats are always global; duration percentiles cover
``completed`` runs only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from server.app.db.transaction import read_connection
from server.app.services._ops_metrics_queue import query_queue_summary

if TYPE_CHECKING:
    from server.app.services.ops_metrics import OpsMetricsService


def query_summary(service: OpsMetricsService, worker_id: str | None = None) -> dict[str, Any]:
    """Compute the summary carried by ``/api/metrics/overview`` responses."""
    worker_key = worker_id if worker_id is not None else ""
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    with read_connection(service._database_dsn) as conn:
        gauges_row = conn.execute(
            "select online_workers, active_executions from ops_metric_samples"
            " where worker_id = ? order by bucket_start desc limit 1",
            (worker_key,),
        ).fetchone()
        tokens = conn.execute(
            """
            select coalesce(sum(input_tokens), 0) as input_tokens,
                   coalesce(sum(output_tokens), 0) as output_tokens,
                   coalesce(sum(cache_read_tokens), 0) as cache_read_tokens,
                   coalesce(sum(total_tokens), 0) as total_tokens
            from ops_metric_samples
            where worker_id = ? and bucket_start >= ?
            """,
            (worker_key, cutoff),
        ).fetchone()
        assert tokens is not None  # aggregate queries always return one row
        runs = conn.execute(
            """
            select count(*) filter (where status = 'completed') as completed,
                   count(*) filter (where status = 'failed') as failed,
                   percentile_cont(0.5) within group (
                     order by extract(epoch from finished_at - started_at)
                   ) filter (where status = 'completed') as p50,
                   percentile_cont(0.95) within group (
                     order by extract(epoch from finished_at - started_at)
                   ) filter (where status = 'completed') as p95
            from node_runs
            where status in ('completed', 'failed') and finished_at >= ?
              and exists (
                select 1 from agent_execution_requests r where r.node_run_id = node_runs.id
              )
            """,
            (cutoff,),
        ).fetchone()
        assert runs is not None  # aggregate queries always return one row
    return {
        "online_workers": (int(gauges_row["online_workers"]) if gauges_row is not None else None),
        "active_executions": (
            int(gauges_row["active_executions"]) if gauges_row is not None else None
        ),
        "recent_hour_tokens": {
            "input_tokens": int(tokens["input_tokens"]),
            "output_tokens": int(tokens["output_tokens"]),
            "cache_read_tokens": int(tokens["cache_read_tokens"]),
            "total_tokens": int(tokens["total_tokens"]),
        },
        "recent_hour_runs": {
            "completed": int(runs["completed"]),
            "failed": int(runs["failed"]),
            "duration_p50_seconds": (float(runs["p50"]) if runs["p50"] is not None else None),
            "duration_p95_seconds": (float(runs["p95"]) if runs["p95"] is not None else None),
        },
        **query_queue_summary(service),
    }
