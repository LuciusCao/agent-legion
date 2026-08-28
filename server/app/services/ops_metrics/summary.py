"""Window-independent summary for the Host operations metrics panel.

Token and gauge values always come from minute-resolution samples
(``bucket_start >= now - 1h`` for tokens, latest minute row for gauges);
run stats aggregate ``node_runs`` on demand (see ``ops_metrics.runs``).
The UI polls this endpoint, so results are cached for a few seconds per
(worker, workspace) scope on the service instance — the run aggregate would
otherwise rescan ``node_runs`` on every poll. ``worker_id`` scopes
gauges/tokens to one Worker; ``workspace_id`` scopes gauges/tokens/runs/
queue to one workspace (schema v23 rows) — the two scopes are not combined.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from server.app.db.transaction import read_connection
from server.app.services.ops_metrics.queue import query_queue_summary
from server.app.services.ops_metrics.runs import query_recent_hour_runs

if TYPE_CHECKING:
    from server.app.services.ops_metrics import OpsMetricsService

_SUMMARY_CACHE_TTL_SECONDS = 5.0
# Distinct (worker, workspace) scopes keep the dict naturally small; the cap
# guards against clients enumerating many ids. Dict get/set are atomic
# enough here — a lost race just recomputes one summary.
_SUMMARY_CACHE_MAX_ENTRIES = 128


def query_summary(
    service: OpsMetricsService,
    worker_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Compute the summary carried by ``/api/metrics/overview`` responses."""
    worker_key = worker_id if worker_id is not None else ""
    workspace_key = workspace_id if workspace_id is not None else ""
    now = datetime.now(UTC)
    cached = service._summary_cache.get((worker_key, workspace_key))
    if cached is not None and (now - cached[0]).total_seconds() < _SUMMARY_CACHE_TTL_SECONDS:
        return cached[1]
    cutoff = now - timedelta(hours=1)
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
    summary = {
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
    if len(service._summary_cache) >= _SUMMARY_CACHE_MAX_ENTRIES:
        service._summary_cache.clear()
    service._summary_cache[(worker_key, workspace_key)] = (now, summary)
    return summary
