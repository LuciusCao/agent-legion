"""Host operations metrics: minute sampling and fixed-window rollups.

A background loop (see ``server.app.startup_tasks.BackgroundTasks``) calls
``sample_once`` every ``monitoring.sample_interval_seconds`` to persist one
global row (``worker_id=''``) plus one row per active Worker per minute into
``ops_metric_samples``; the ``/api/metrics/overview`` route serves raw minute
rows or epoch-floor rollups from the same table. The ``granularity`` query
value names the window (``6h``/``24h``/``30d``) and implies the bin size
(60s/300s/14400s); there are no separate window params. The response also
carries a window-independent ``summary`` (see ``ops_metrics.summary``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from server.app.agent_broker import worker_events
from server.app.agent_control.registry import ONLINE_THRESHOLD_SECONDS as _ONLINE_THRESHOLD_SECONDS
from server.app.db.connection import DatabaseConnection
from server.app.db.dialect import ConnectSource
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.ops_metrics.catchup import sample_catch_up as _sample_catch_up
from server.app.services.ops_metrics.sampling import _EMPTY_TOKENS, _upsert_sample
from server.app.services.ops_metrics.sampling_dispatch import (
    sample_runtime_profile as _sample_runtime_profile,
)
from server.app.services.ops_metrics.series import query_series as _query_series
from server.app.services.ops_metrics.summary import query_summary as _query_summary
from server.app.services.ops_metrics.workspace_sampling import (
    collect_workspace_samples,
    upsert_workspace_samples,
)

Granularity = Literal["6h", "24h", "30d"]

_DEFAULT_SAMPLE_INTERVAL_SECONDS = 60
_DEFAULT_RETENTION_DAYS = 30


def _fetch_one(conn: DatabaseConnection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    row = conn.execute(sql, params).fetchone()
    assert row is not None
    return row


class OpsMetricsService:
    def __init__(self, database_dsn: ConnectSource, config: dict[str, Any]) -> None:
        self._database_dsn = database_dsn
        monitoring = config.get("monitoring", {})
        self._sample_interval_seconds = float(
            monitoring.get("sample_interval_seconds", _DEFAULT_SAMPLE_INTERVAL_SECONDS)
        )
        self._retention_days = int(monitoring.get("retention_days", _DEFAULT_RETENTION_DAYS))
        self._summary_cache: dict[tuple[str, str], tuple[datetime, dict[str, Any]]] = {}

    @property
    def sample_interval_seconds(self) -> float:
        return self._sample_interval_seconds

    @property
    def database_dsn(self) -> ConnectSource:
        """Read accessor for co-sampling surfaces (runtime profile, #359)."""
        return self._database_dsn

    def sample_once(self, now: datetime | None = None) -> None:
        """Persist samples for the last completed UTC minute bucket."""
        sampled_at = now or datetime.now(UTC)
        bucket_start = sampled_at.replace(second=0, microsecond=0) - timedelta(minutes=1)
        bucket_end = bucket_start + timedelta(minutes=1)
        online_since = sampled_at - timedelta(seconds=_ONLINE_THRESHOLD_SECONDS)
        with read_connection(self._database_dsn) as conn:
            online_workers = _fetch_one(
                conn,
                "select count(*) as c from agent_workers"
                " where revoked_at is null and last_seen_at >= %s",
                (online_since,),
            )["c"]
            worker_rows = conn.execute(
                "select worker_id, last_seen_at from agent_workers where revoked_at is null"
            ).fetchall()
            worker_last_seen = {str(r["worker_id"]): r["last_seen_at"] for r in worker_rows}
            online_worker_ids = {
                k
                for k, v in worker_last_seen.items()
                if (t := worker_events.as_utc(v)) is not None and t >= online_since
            }
            active_executions = _fetch_one(
                conn, "select count(*) as c from agent_execution_requests where state = 'claimed'"
            )["c"]
            queued = _fetch_one(
                conn, "select count(*) as c from agent_execution_requests where state = 'queued'"
            )["c"]
            claimed_by_worker = {
                row["worker_id"]: row["c"]
                for row in conn.execute(
                    "select worker_id, count(*) as c from agent_execution_requests"
                    " where state = 'claimed' and worker_id is not null"
                    " group by worker_id"
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
                where created_at >= %s and created_at < %s
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
                    where u.created_at >= %s and u.created_at < %s
                      and r.worker_id is not null
                    group by r.worker_id
                    """,
                    (bucket_start, bucket_end),
                ).fetchall()
            }
            workspace_samples = collect_workspace_samples(conn, bucket_start, bucket_end)
        with write_transaction(self._database_dsn) as conn:
            _upsert_sample(
                conn,
                bucket_start,
                "",
                online_workers=online_workers,
                active_executions=active_executions,
                tokens=tokens,
                queued=int(queued),
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
            upsert_workspace_samples(conn, bucket_start, workspace_samples)
        worker_events.note_worker_offline(worker_last_seen, online_since)
        self._sample_runtime_profile(bucket_start, queued=int(queued), active=active_executions)

    def _sample_runtime_profile(self, bucket_start: datetime, *, queued: int, active: int) -> None:
        """Persist best-effort runtime-profile gauges for one bucket."""
        _sample_runtime_profile(self._database_dsn, bucket_start, queued=queued, active=active)

    def sample_catch_up(self, now: datetime | None = None) -> int:
        """Persist every missing minute bucket since the last written sample."""
        return _sample_catch_up(self, now)

    def cleanup_expired(self, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now(UTC)) - timedelta(days=self._retention_days)
        with write_transaction(self._database_dsn) as conn:
            result = conn.execute(
                "delete from ops_metric_samples where bucket_start < %s", (cutoff,)
            )
        from server.app.jobs.queries.runtime_profile import runtime_profile_queries_from_dsn

        runtime_profile_queries_from_dsn(self._database_dsn).delete_runtime_profile_samples_before(
            cutoff
        )
        return result.rowcount

    def query_summary(
        self, worker_id: str | None = None, workspace_id: str | None = None
    ) -> dict[str, Any]:
        return _query_summary(self, worker_id, workspace_id)

    def query_series(
        self,
        granularity: Granularity,
        worker_id: str | None = None,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return _query_series(self, granularity, worker_id, workspace_id)
