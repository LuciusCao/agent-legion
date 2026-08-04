"""Catch-up sampling for the Host operations metrics service.

Split out of ``ops_metrics.py`` to respect that module's size budget. The
sampling loop's cycle is sample work + sleep, so under load it drifts past
minute boundaries; writing only "the previous minute" permanently skips
buckets (visible as gaps on the monitoring panel). ``sample_catch_up``
backfills every missing bucket since the last written sample, capped at
``_MAX_BACKFILL_MINUTES``: after a longer outage the gap stays (no samples
existed), and online/active numbers are only meaningful near "now" anyway.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from server.app.db.transaction import read_connection

if TYPE_CHECKING:
    from server.app.services.ops_metrics import OpsMetricsService

_MAX_BACKFILL_MINUTES = 10


def _parse_bucket_start(value: Any) -> datetime:
    # Accept both the row factory's ISO-8601 UTC strings and legacy naive
    # "%Y-%m-%d %H:%M:%S.%f" strings from TEXT columns.
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def sample_catch_up(service: OpsMetricsService, now: datetime | None = None) -> int:
    """Persist every missing minute bucket since the last written sample."""
    sampled_at = now or datetime.now(UTC)
    target = sampled_at.replace(second=0, microsecond=0) - timedelta(minutes=1)
    with read_connection(service._database_dsn) as conn:
        row = conn.execute(
            "select max(bucket_start) as last from ops_metric_samples where worker_id=''"
        ).fetchone()
    last = _parse_bucket_start(row["last"]) if row is not None and row["last"] else None
    earliest = target - timedelta(minutes=_MAX_BACKFILL_MINUTES)
    start = earliest if last is None else max(last, earliest)
    written = 0
    bucket = start + timedelta(minutes=1)
    while bucket <= target:
        service.sample_once(now=bucket + timedelta(minutes=1))
        written += 1
        bucket += timedelta(minutes=1)
    return written
