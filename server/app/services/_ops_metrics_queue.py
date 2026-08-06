"""Queue-health summary for the ops-metrics panel (issue #13).

``blocked`` (fresh signal from the empty-claim trigger; fleet-level, shown in
a workspace view only while that workspace has queued rows) vs ``stalled``
(queued rows, zero activity, no claim attempts at all) — the classification
itself lives in ``_ops_metrics_queue_alert.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from server.app.db.transaction import read_connection
from server.app.services._ops_metrics_queue_alert import queue_alert
from server.app.services._ops_metrics_series import _isoformat_utc

if TYPE_CHECKING:
    from server.app.services.ops_metrics import OpsMetricsService


def query_queue_summary(
    service: OpsMetricsService, workspace_id: str | None = None
) -> dict[str, Any]:
    """Compute queue depth, sweeper disposals and the queue alert."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=1)
    ws_filter = workspace_id is not None
    ws = workspace_id or ""
    with read_connection(service._database_dsn) as conn:
        queue = conn.execute(
            "select count(*) as c, min(queued_at) as oldest"
            " from agent_execution_requests where state='queued'"
            + (" and workspace_id=%s" if ws_filter else ""),
            (ws,) if ws_filter else (),
        ).fetchone()
        assert queue is not None  # aggregate queries always return one row
        gauges = conn.execute(
            "select online_workers, active_executions from ops_metric_samples"
            " where worker_id='' and workspace_id=%s order by bucket_start desc limit 1",
            (ws if ws_filter else "",),
        ).fetchone()
        fleet = conn.execute(
            "select online_workers from ops_metric_samples"
            " where worker_id='' and workspace_id='' order by bucket_start desc limit 1"
        ).fetchone()
        swept = conn.execute(
            "select count(*) as c from job_nodes"
            " where failure_detail='unclaimable_model' and finished_at >= %s"
            + (
                " and exists (select 1 from jobs j where j.id = job_nodes.job_id"
                " and j.workspace_id = %s)"
                if ws_filter
                else ""
            ),
            (cutoff, ws) if ws_filter else (cutoff,),
        ).fetchone()
        assert swept is not None  # aggregate queries always return one row
        signal = conn.execute(
            "select kind, reasons_json, updated_at from agent_queue_signals where id=1"
        ).fetchone()
    return {
        "queue": {
            "queued": int(queue["c"]),
            "oldest_queued_at": (
                _isoformat_utc(queue["oldest"]) if queue["oldest"] is not None else None
            ),
            "recent_hour_unclaimable_failed": int(swept["c"]),
        },
        "queue_alert": queue_alert(
            now=now,
            queued=int(queue["c"]),
            oldest=queue["oldest"],
            active=int(gauges["active_executions"]) if gauges is not None else 0,
            online=int(fleet["online_workers"]) if fleet is not None else 0,
            signal=signal,
        ),
    }
