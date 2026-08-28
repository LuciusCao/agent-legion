"""Bucketed series queries for the Host operations metrics service.

Reads minute rows or epoch-floor rollups from ``ops_metric_samples`` for one
metric scope (global aggregate row or a single Worker). The window table and
the UTC ISO row formatter live here with the query; the ``ops_metrics``
package re-exports them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from server.app.db.transaction import read_connection

if TYPE_CHECKING:
    from server.app.services.ops_metrics import Granularity, OpsMetricsService

# granularity -> (回看窗口, 聚合桶秒数)；6h 直接返回逐分钟原始行。
_WINDOWS: dict[str, tuple[timedelta, int]] = {
    "6h": (timedelta(hours=6), 60),
    "24h": (timedelta(hours=24), 300),
    "30d": (timedelta(days=30), 14_400),
}


# The shared row factory renders datetimes as UTC ISO-8601 strings with an
# explicit offset (see server.app.db.rows); legacy rows and TEXT columns may
# still hold naive "%Y-%m-%d %H:%M:%S.%f" strings. Accept either shape and
# emit ISO-8601 with an explicit UTC offset for API consumers.
def _isoformat_utc(value: Any) -> str:
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value))
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return str(value.isoformat())


def query_series(
    service: OpsMetricsService,
    granularity: Granularity,
    worker_id: str | None = None,
    workspace_id: str | None = None,
) -> list[dict[str, Any]]:
    """Read minute rows or epoch-floor rollups for one metric scope.

    ``granularity`` names the lookback window and implies the bin size
    (``6h``→60s raw rows, ``24h``→300s, ``30d``→14400s). ``worker_id=None``
    reads the global aggregate rows (``worker_id=''``); any other value
    reads only that Worker's per-worker samples. ``workspace_id`` selects
    per-workspace rows (schema v23); the two scopes are not combined.
    """
    worker_key = worker_id if worker_id is not None else ""
    workspace_key = workspace_id if workspace_id is not None else ""
    window, bin_seconds = _WINDOWS[granularity]
    cutoff = datetime.now(UTC) - window
    if bin_seconds == 60:
        sql = """
            select bucket_start,
                   online_workers, online_workers as online_workers_max,
                   active_executions, active_executions as active_executions_max,
                   queued, queued as queued_max,
                   input_tokens, output_tokens, cache_read_tokens, total_tokens
            from ops_metric_samples
            where bucket_start >= %s and worker_id = %s and workspace_id = %s
            order by bucket_start
            """
    else:
        # epoch-floor 分桶（UTC，与 date_trunc 不同不受会话时区影响）：
        # 5 分钟 / 4 小时桶边界分别对齐 :00/:05… 与 00/04/08/12/16/20 UTC。
        sql = f"""
            select to_timestamp(
                     floor(extract(epoch from bucket_start) / {bin_seconds}) * {bin_seconds}
                   ) as bucket_start,
                   round(avg(online_workers)) as online_workers,
                   max(online_workers) as online_workers_max,
                   round(avg(active_executions)) as active_executions,
                   max(active_executions) as active_executions_max,
                   round(avg(queued)) as queued,
                   max(queued) as queued_max,
                   sum(input_tokens) as input_tokens,
                   sum(output_tokens) as output_tokens,
                   sum(cache_read_tokens) as cache_read_tokens,
                   sum(total_tokens) as total_tokens
            from ops_metric_samples
            where bucket_start >= %s and worker_id = %s and workspace_id = %s
            group by 1
            order by 1
            """
    with read_connection(service._database_dsn) as conn:
        rows = conn.execute(sql, (cutoff, worker_key, workspace_key)).fetchall()
    return [
        {
            "bucket_start": _isoformat_utc(row["bucket_start"]),
            "online_workers": int(row["online_workers"]),
            "online_workers_max": int(row["online_workers_max"]),
            "active_executions": int(row["active_executions"]),
            "active_executions_max": int(row["active_executions_max"]),
            "queued": int(row["queued"]),
            "queued_max": int(row["queued_max"]),
            "input_tokens": int(row["input_tokens"]),
            "output_tokens": int(row["output_tokens"]),
            "cache_read_tokens": int(row["cache_read_tokens"]),
            "total_tokens": int(row["total_tokens"]),
        }
        for row in rows
    ]
