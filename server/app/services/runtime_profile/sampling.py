"""Persistence and reads for the runtime-profile gauge table (#359 L1).

``persist_profile_sample`` merges the process counters snapshot with the
depth gauges that live outside the process (queued rows, active executions
from the DB; pool backlog from the enqueue pool) and upserts one row per
minute bucket. ``query_profile_series`` serves the recent buckets for the
profile UI and the classifier. All SQL goes through the JobQueries facade
(``RuntimeProfileQueriesMixin``) — this module only prepares values
(BOUNDARY-DATA-001).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from server.app.db.dialect import ConnectSource
from server.app.jobs.queries.runtime_profile import runtime_profile_queries_from_dsn
from server.app.services.runtime_profile.counters import RuntimeProfile


def persist_profile_sample(
    dsn: ConnectSource,
    bucket_start: datetime,
    runtime_profile: RuntimeProfile,
    *,
    queued_depth: int,
    active_executions: int,
    enqueue_pending: int,
    db_pool_waiting: int = 0,
    db_pool_wait_seconds: float = 0.0,
) -> None:
    """Upsert one global gauge row for ``bucket_start``.

    Depth gauges arrive as arguments (they are point-in-time reads, not
    per-bucket deltas); everything else comes from the process counters
    snapshot, which this call resets. Upsert semantics mirror
    ``ops_metric_samples``: a sampler restart inside the same minute
    overwrites the partial bucket rather than double-counting.
    """
    deltas = runtime_profile.counters.snapshot_and_reset()
    values: dict[str, Any] = {
        "intake_runs": deltas["intake_runs"],
        "intake_items": deltas["intake_items"],
        "pass_count": deltas["pass_count"],
        "pass_seconds_total": deltas["pass_seconds_total"],
        "pass_scan_seconds_max": deltas["pass_scan_seconds_max"],
        "pass_slow_count": deltas["pass_slow_count"],
        "enqueue_submitted": deltas["enqueue_submitted"],
        "enqueue_pool_skipped": deltas["enqueue_pool_skipped"],
        "enqueue_pending": enqueue_pending,
        "enqueue_stock_gated": deltas["enqueue_stock_gated"],
        "claim_count": deltas["claim_count"],
        "claim_empty_count": deltas["claim_empty_count"],
        "claim_seconds_total": deltas["claim_seconds_total"],
        "claim_seconds_max": deltas["claim_seconds_max"],
        # Claim-stage split (schema v78, #448): scan/evaluate/writes.
        **{
            f"claim_{stage}_seconds_{kind}": deltas[f"claim_{stage}_seconds_{kind}"]
            for stage in ("scan", "evaluate", "writes")
            for kind in ("total", "max")
        },
        "execute_active": active_executions,
        "execute_done": deltas["execute_done"],
        "execute_requeued": deltas["execute_requeued"],
        "result_count": deltas["result_count"],
        "result_seconds_total": deltas["result_seconds_total"],
        "result_seconds_max": deltas["result_seconds_max"],
        "db_pool_waiting": db_pool_waiting,
        "db_pool_wait_seconds_total": db_pool_wait_seconds,
    }
    runtime_profile_queries_from_dsn(dsn).upsert_runtime_profile_sample(bucket_start, values)


# Columns aggregated by max when rolling minute rows up into wider bins
# (latencies and momentary depths); everything else sums. The claim-stage
# maxes (#448) ride the same suffix rule as claim_seconds_max.
_MAX_AGGREGATED_COLUMNS = frozenset(
    {
        "pass_scan_seconds_max",
        "claim_seconds_max",
        "result_seconds_max",
        "execute_active",
        "enqueue_pending",
        "db_pool_waiting",
    }
)


def _aggregates_by_max(column: str) -> bool:
    # "_seconds_max" covers claim/result/pass latency peaks alike.
    return column in _MAX_AGGREGATED_COLUMNS or column.endswith("_seconds_max")


def _rollup(rows: list[dict[str, Any]], bin_seconds: int) -> list[dict[str, Any]]:
    """Fold minute rows into fixed bins (epoch-floor, like ops series)."""
    if bin_seconds <= 60:
        return rows
    bins: dict[int, dict[str, Any]] = {}
    for row in rows:
        start = row["bucket_start"]
        # The row factory may render timestamptz as a string depending on
        # the connection's session timezone; normalize before epoch-floor.
        if isinstance(start, str):
            start = datetime.fromisoformat(start)
        key = int(start.timestamp()) // bin_seconds * bin_seconds
        if key not in bins:
            bins[key] = dict(row)
            continue
        acc = bins[key]
        for column, value in row.items():
            if column == "bucket_start" or value is None:
                continue
            if _aggregates_by_max(column):
                acc[column] = max(acc[column] or 0, value)
            else:
                acc[column] = (acc[column] or 0) + value
    return [bins[key] for key in sorted(bins)]


def query_profile_series(
    dsn: ConnectSource, buckets: int = 30, bin_seconds: int = 60
) -> list[dict[str, Any]]:
    """Read the most recent ``buckets`` gauge rows, oldest first.

    ``bin_seconds > 60`` rolls minute rows up into fixed epoch-floor bins
    (latencies/depths take the max, counters sum) so a wide window stays a
    bounded response — same shape as the ops-metrics series rollup.
    """
    rows = runtime_profile_queries_from_dsn(dsn).recent_runtime_profile_samples(buckets)
    return _rollup(rows, bin_seconds)
