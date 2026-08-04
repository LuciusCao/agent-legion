"""Queue-health summary for the ops-metrics panel (issue #13).

Split out of ``_ops_metrics_summary.py`` for the file-size budget. Answers
the incident question "负载掉了，为什么" from database state alone:

- ``blocked``: the empty-claim trigger persisted a fresh signal — claims are
  being attempted but every candidate is skipped; the reason histogram says
  why (model/capability mismatch, paused, …).
- ``stalled``: queued rows are piling up with zero running executions and no
  claim activity at all — the class the blocked signal cannot see, e.g. the
  only compatible Worker has claiming switched off locally.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from server.app.db.transaction import read_connection
from server.app.services._ops_metrics_series import _isoformat_utc

if TYPE_CHECKING:
    from server.app.services.ops_metrics import OpsMetricsService

# A blocked signal fresher than this drives the red banner; older signals are
# considered recovered and self-expire (nobody ever clears the row).
_BLOCKED_SIGNAL_FRESH = timedelta(minutes=10)
# Grace window before a non-zero queue with zero activity counts as stalled:
# absorbs the enqueue → next-worker-poll gap and brief sampler pauses.
_STALLED_MIN_AGE = timedelta(minutes=2)


def _aware(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def query_queue_summary(service: OpsMetricsService) -> dict[str, Any]:
    """Compute queue depth, sweeper disposals and the queue alert."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=1)
    with read_connection(service._database_dsn) as conn:
        queue = conn.execute(
            "select count(*) as c, min(queued_at) as oldest"
            " from agent_execution_requests where state='queued'"
        ).fetchone()
        assert queue is not None  # aggregate queries always return one row
        gauges = conn.execute(
            "select online_workers, active_executions from ops_metric_samples"
            " where worker_id='' order by bucket_start desc limit 1"
        ).fetchone()
        swept = conn.execute(
            "select count(*) as c from job_nodes"
            " where failure_detail='unclaimable_model' and finished_at >= %s",
            (cutoff,),
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
        "queue_alert": _queue_alert(
            now=now,
            queued=int(queue["c"]),
            oldest=queue["oldest"],
            active=int(gauges["active_executions"]) if gauges is not None else 0,
            online=int(gauges["online_workers"]) if gauges is not None else 0,
            signal=signal,
        ),
    }


def _queue_alert(
    *,
    now: datetime,
    queued: int,
    oldest: Any,
    active: int,
    online: int,
    signal: Any,
) -> dict[str, Any] | None:
    if signal is not None:
        updated_at = _aware(signal["updated_at"])
        if now - updated_at <= _BLOCKED_SIGNAL_FRESH:
            return {
                "kind": str(signal["kind"]),
                "at": _isoformat_utc(updated_at),
                "reasons": {
                    str(key): int(value)
                    for key, value in json.loads(str(signal["reasons_json"] or "{}")).items()
                },
            }
    if (
        queued > 0
        and active == 0
        and online > 0
        and oldest is not None
        and now - _aware(oldest) >= _STALLED_MIN_AGE
    ):
        return {"kind": "stalled", "at": None, "reasons": {}}
    return None
