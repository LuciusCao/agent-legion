"""Host operations metrics: minute sampling and minute/hour/day rollups.

A background loop (see ``server.app.startup_tasks.BackgroundTasks``) calls
``sample_once`` every ``monitoring.sample_interval_seconds`` to persist one
global row (``worker_id=''``) plus one row per active Worker per minute into
``ops_metric_samples``; the ``/api/metrics/overview`` route serves raw minute
rows or hour/day rollups from the same table.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from server.app.agent_workers import _ONLINE_THRESHOLD_SECONDS
from server.app.db.connection import DatabaseConnection, DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction

Granularity = Literal["minute", "hour", "day"]

_DEFAULT_SAMPLE_INTERVAL_SECONDS = 60
_DEFAULT_RETENTION_DAYS = 7

# The shared row factory renders datetimes as UTC "%Y-%m-%d %H:%M:%S.%f"
# strings (see server.app.db.rows); accept either shape and emit ISO-8601
# with an explicit UTC offset for API consumers.
_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


def _isoformat_utc(value: Any) -> str:
    if not isinstance(value, datetime):
        value = datetime.strptime(str(value), _TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    elif value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return str(value.isoformat())


def _fetch_one(conn: DatabaseConnection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    row = conn.execute(sql, params).fetchone()
    assert row is not None  # aggregate queries always return one row
    return row


_EMPTY_TOKENS: dict[str, int] = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "total_tokens": 0,
}


def _upsert_sample(
    conn: DatabaseConnection,
    bucket_start: datetime,
    worker_id: str,
    *,
    online_workers: int,
    active_executions: int,
    tokens: dict[str, Any],
) -> None:
    conn.execute(
        """
        insert into ops_metric_samples(
          bucket_start, worker_id, online_workers, active_executions,
          input_tokens, output_tokens, cache_read_tokens, total_tokens
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        on conflict (bucket_start, worker_id) do update set
          online_workers=excluded.online_workers,
          active_executions=excluded.active_executions,
          input_tokens=excluded.input_tokens,
          output_tokens=excluded.output_tokens,
          cache_read_tokens=excluded.cache_read_tokens,
          total_tokens=excluded.total_tokens
        """,
        (
            bucket_start,
            worker_id,
            online_workers,
            active_executions,
            tokens["input_tokens"],
            tokens["output_tokens"],
            tokens["cache_read_tokens"],
            tokens["total_tokens"],
        ),
    )


class OpsMetricsService:
    def __init__(self, database_dsn: DatabaseDsn, config: dict[str, Any]) -> None:
        self._database_dsn = database_dsn
        monitoring = config.get("monitoring", {})
        self._sample_interval_seconds = float(
            monitoring.get("sample_interval_seconds", _DEFAULT_SAMPLE_INTERVAL_SECONDS)
        )
        self._retention_days = int(monitoring.get("retention_days", _DEFAULT_RETENTION_DAYS))

    @property
    def sample_interval_seconds(self) -> float:
        return self._sample_interval_seconds

    def sample_once(self, now: datetime | None = None) -> None:
        """Persist samples for the last completed UTC minute bucket.

        Writes the global aggregate row (``worker_id=''``) plus one row per
        Worker with any current activity: online (unrevoked, seen within the
        online threshold) or holding a claimed execution. Per-Worker tokens
        come from token-usage rows joined to the claiming execution request;
        tokens from Host-local runs (no ``agent_execution_requests`` row)
        land only in the global row, so ``sum(per-worker) <= global``.

        Only currently active Workers get a row for the bucket: a Worker
        that goes idle simply stops appearing in later buckets, and buckets
        already written are never revised (upserts only refresh rows for the
        current active set).
        """
        sampled_at = now or datetime.now(UTC)
        bucket_start = sampled_at.replace(second=0, microsecond=0) - timedelta(minutes=1)
        bucket_end = bucket_start + timedelta(minutes=1)
        online_since = sampled_at - timedelta(seconds=_ONLINE_THRESHOLD_SECONDS)
        with read_connection(self._database_dsn) as conn:
            online_workers = _fetch_one(
                conn,
                "select count(*) as c from agent_workers"
                " where revoked_at is null and last_seen_at >= ?",
                (online_since,),
            )["c"]
            online_worker_ids = {
                row["worker_id"]
                for row in conn.execute(
                    "select worker_id from agent_workers"
                    " where revoked_at is null and last_seen_at >= ?",
                    (online_since,),
                ).fetchall()
            }
            active_executions = _fetch_one(
                conn,
                "select count(*) as c from agent_execution_requests where state = 'claimed'",
            )["c"]
            claimed_by_worker = {
                row["worker_id"]: row["c"]
                for row in conn.execute(
                    "select worker_id, count(*) as c from agent_execution_requests"
                    " where state = 'claimed' and worker_id is not null"
                    " group by worker_id",
                ).fetchall()
            }
            tokens = _fetch_one(
                conn,
                """
                select coalesce(sum(input_tokens), 0) as input_tokens,
                       coalesce(sum(output_tokens), 0) as output_tokens,
                       coalesce(sum(cache_read_tokens), 0) as cache_read_tokens,
                       coalesce(sum(total_tokens), 0) as total_tokens
                from node_run_token_usage
                where created_at >= ? and created_at < ?
                """,
                (bucket_start, bucket_end),
            )
            tokens_by_worker = {
                row["worker_id"]: row
                for row in conn.execute(
                    """
                    select r.worker_id,
                           coalesce(sum(u.input_tokens), 0) as input_tokens,
                           coalesce(sum(u.output_tokens), 0) as output_tokens,
                           coalesce(sum(u.cache_read_tokens), 0) as cache_read_tokens,
                           coalesce(sum(u.total_tokens), 0) as total_tokens
                    from node_run_token_usage u
                    join agent_execution_requests r on r.node_run_id = u.node_run_id
                    where u.created_at >= ? and u.created_at < ?
                      and r.worker_id is not null
                    group by r.worker_id
                    """,
                    (bucket_start, bucket_end),
                ).fetchall()
            }
        with write_transaction(self._database_dsn) as conn:
            _upsert_sample(
                conn,
                bucket_start,
                "",
                online_workers=online_workers,
                active_executions=active_executions,
                tokens=tokens,
            )
            for worker_id in sorted(online_worker_ids | claimed_by_worker.keys()):
                _upsert_sample(
                    conn,
                    bucket_start,
                    worker_id,
                    online_workers=1 if worker_id in online_worker_ids else 0,
                    active_executions=claimed_by_worker.get(worker_id, 0),
                    tokens=tokens_by_worker.get(worker_id, _EMPTY_TOKENS),
                )

    def cleanup_expired(self, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now(UTC)) - timedelta(days=self._retention_days)
        with write_transaction(self._database_dsn) as conn:
            result = conn.execute(
                "delete from ops_metric_samples where bucket_start < ?", (cutoff,)
            )
            return result.rowcount

    def query_series(
        self,
        granularity: Granularity,
        hours: int,
        days: int,
        worker_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read minute rows or hour/day rollups for one metric scope.

        ``worker_id=None`` reads the global aggregate rows (``worker_id=''``);
        any other value reads only that Worker's per-worker samples.
        """
        worker_key = worker_id if worker_id is not None else ""
        if granularity == "minute":
            cutoff = datetime.now(UTC) - timedelta(hours=hours)
            sql = """
                select bucket_start,
                       online_workers, online_workers as online_workers_max,
                       active_executions, active_executions as active_executions_max,
                       input_tokens, output_tokens, cache_read_tokens, total_tokens
                from ops_metric_samples
                where bucket_start >= ? and worker_id = ?
                order by bucket_start
                """
        else:
            unit = "hour" if granularity == "hour" else "day"
            cutoff = datetime.now(UTC) - (
                timedelta(hours=hours) if granularity == "hour" else timedelta(days=days)
            )
            sql = f"""
                select date_trunc('{unit}', bucket_start) as bucket_start,
                       round(avg(online_workers)) as online_workers,
                       max(online_workers) as online_workers_max,
                       round(avg(active_executions)) as active_executions,
                       max(active_executions) as active_executions_max,
                       sum(input_tokens) as input_tokens,
                       sum(output_tokens) as output_tokens,
                       sum(cache_read_tokens) as cache_read_tokens,
                       sum(total_tokens) as total_tokens
                from ops_metric_samples
                where bucket_start >= ? and worker_id = ?
                group by 1
                order by 1
                """
        with read_connection(self._database_dsn) as conn:
            rows = conn.execute(sql, (cutoff, worker_key)).fetchall()
        return [
            {
                "bucket_start": _isoformat_utc(row["bucket_start"]),
                "online_workers": int(row["online_workers"]),
                "online_workers_max": int(row["online_workers_max"]),
                "active_executions": int(row["active_executions"]),
                "active_executions_max": int(row["active_executions_max"]),
                "input_tokens": int(row["input_tokens"]),
                "output_tokens": int(row["output_tokens"]),
                "cache_read_tokens": int(row["cache_read_tokens"]),
                "total_tokens": int(row["total_tokens"]),
            }
            for row in rows
        ]
