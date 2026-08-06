"""Window-independent summary for the Host operations metrics panel.

Split out of ``ops_metrics.py`` to respect that module's size budget. The
summary feeds the monitoring cards, which must stay stable while the chart
window switches: token and gauge values always come from minute-resolution
samples (``bucket_start >= now - 1h`` for tokens, latest minute row for
gauges), and run stats are aggregated on demand from ``node_runs`` (see
``_ops_metrics_runs``). ``worker_id`` scopes gauges/tokens to one Worker;
``workspace_id`` scopes gauges/tokens/runs/queue to one workspace (schema
v23 per-workspace rows) — the two scopes are not combined.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from server.app.db.transaction import read_connection
from server.app.services._ops_metrics_queue import query_queue_summary
from server.app.services._ops_metrics_runs import query_recent_hour_runs

if TYPE_CHECKING:
    from server.app.services.ops_metrics import OpsMetricsService


def query_summary(
    service: OpsMetricsService,
    worker_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Compute the summary carried by ``/api/metrics/overview`` responses."""
    worker_key = worker_id if worker_id is not None else ""
    workspace_key = workspace_id if workspace_id is not None else ""
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    with read_connection(service._database_dsn) as conn:
        gauges_row = conn.execute(
            "select online_workers, active_executions from ops_metric_samples"
            " where worker_id = %s and workspace_id = %s"
            " order by bucket_start desc limit 1",
            (worker_key, workspace_key),
        ).fetchone()
        tokens = conn.execute(
            """
            select coalesce(sum(input_tokens), 0) as input_tokens,
                   coalesce(sum(output_tokens), 0) as output_tokens,
                   coalesce(sum(cache_read_tokens), 0) as cache_read_tokens,
                   coalesce(sum(total_tokens), 0) as total_tokens
            from ops_metric_samples
            where worker_id = %s and workspace_id = %s and bucket_start >= %s
            """,
            (worker_key, workspace_key, cutoff),
        ).fetchone()
        assert tokens is not None  # aggregate queries always return one row
        runs = query_recent_hour_runs(conn, cutoff, workspace_id)
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
        "recent_hour_runs": runs,
        **query_queue_summary(service, workspace_id),
    }
