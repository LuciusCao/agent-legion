"""Wide-window bin rollup for runtime-profile rows (#359, #521).

Minute rows fold into fixed epoch-floor bins (the same shape the
ops-metrics series rollup uses) so a wide window stays a bounded response:
latencies and momentary depths aggregate by max, everything else sums.
``_aggregates_by_max``'s suffix rule means any new ``*_seconds_max`` gauge
family (the #448 claim stages, the #521 result stages) rides along without
a list edit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

# Columns aggregated by max when rolling minute rows up into wider bins
# (latencies and momentary depths); everything else sums.
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


def rollup_rows(rows: list[dict[str, Any]], bin_seconds: int) -> list[dict[str, Any]]:
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
