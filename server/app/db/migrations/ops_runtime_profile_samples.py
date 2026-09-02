"""Runtime profile samples (schema v72, issue #359).

The runtime-profile pipeline gauge table for the L1 execution-pipeline
metrics: one row per minute bucket on the global scope (``worker_id=''``)
carrying the six-stage depth/rate/latency gauges plus the cross-cutting DB
pool and advisory-lock waits. Written by the ops-metrics sampling loop
alongside ``ops_metric_samples``; retention rides the same
``monitoring.retention_days`` cleanup (``OpsMetricsService.cleanup_expired``
also deletes from this table), so no separate sweeper exists.
"""

from __future__ import annotations

from typing import Any

_BACKFILL_SQL = """
insert into ops_runtime_profile_samples(bucket_start)
select bucket_start from ops_metric_samples where worker_id='' and workspace_id=''
on conflict (bucket_start) do nothing
"""


def migrate_ops_runtime_profile_samples(conn: Any) -> None:
    """Create the runtime-profile gauge rows for existing buckets (v72).

    The DDL itself comes from the schema-file replay (create-table-if-not-
    exists); this apply fn only seeds bucket rows so the profile series
    starts aligned with the existing ops series instead of at the next
    minute boundary. Gauge columns keep their defaults (0 / null latency)
    — pre-migration buckets genuinely had no instrumentation, and the
    classifier must not read fabricated latencies out of them.
    """
    conn.execute(_BACKFILL_SQL)
