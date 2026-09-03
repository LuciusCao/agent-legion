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

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from server.app.agent_control.registry import ONLINE_THRESHOLD_SECONDS as _ONLINE_THRESHOLD_SECONDS
from server.app.db.connection import DatabaseConnection
from server.app.db.dialect import ConnectSource
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.ops_metrics.catchup import sample_catch_up as _sample_catch_up
from server.app.services.ops_metrics.sampling import _EMPTY_TOKENS, _upsert_sample
from server.app.services.ops_metrics.series import query_series as _query_series
from server.app.services.ops_metrics.summary import query_summary as _query_summary
from server.app.services.ops_metrics.workspace_sampling import (
    collect_workspace_samples,
    upsert_workspace_samples,
)

Granularity = Literal["6h", "24h", "30d"]

_DEFAULT_SAMPLE_INTERVAL_SECONDS = 60
# 30d 窗口需要至少 30 天的采样保留，否则长尾段永远为空。
_DEFAULT_RETENTION_DAYS = 30

logger = logging.getLogger(__name__)

# Last-read cumulative pool counters (module state: one sampler per process).
# psycopg-pool reports lifetime cumulatives (requests_queued /
# requests_wait_ms count every getconn since pool creation); the profile
# buckets need the per-minute DELTA, so each read remembers the previous
# value. Empirically verified against psycopg-pool 3.3: the getconn/putconn
# path this repo uses (server/app/db/connection.py) accumulates both keys —
# usage_ms does NOT grow on this path (only the `pool.connection()` context
# manager updates it), so it must not be used as the wait signal.
_DB_POOL_LAST_STATS: dict[str, int] = {}


def _db_pool_wait_gauges(database_dsn: ConnectSource) -> tuple[int, float]:
    """Per-bucket pool-wait deltas (best-effort; never raises).

    Keyed by ``resolve_dsn`` — a facade's ``str()`` is an object repr that
    would mint a fresh never-used pool reporting zeros (P0 on #367).
    """
    try:
        from server.app.db.dialect import resolve_dsn
        from server.app.db.pools import pool_for

        stats = pool_for(resolve_dsn(database_dsn)).get_stats()
        queued = int(stats.get("requests_queued", 0) or 0)
        wait_ms = int(stats.get("requests_wait_ms", 0) or 0)
        delta_queued = max(queued - _DB_POOL_LAST_STATS.get("queued", 0), 0)
        delta_wait_ms = max(wait_ms - _DB_POOL_LAST_STATS.get("wait_ms", 0), 0)
        _DB_POOL_LAST_STATS["queued"] = queued
        _DB_POOL_LAST_STATS["wait_ms"] = wait_ms
        return delta_queued, delta_wait_ms / 1000.0
    except Exception:
        # #204 broad-except audit: gauge reader, not a pipeline component.
        # A failure means "no DB-pool signal this bucket", never a sampling
        # abort; the traceback is not actionable beyond the log line.
        return 0, 0.0


def _enqueue_pending_depth() -> int:
    """Momentary combined enqueue backlog (0 when unwired).

    Both pools count (agent bundling + code bundling; P1 on #367) — the
    worker thread registers them on the profile at startup.
    """
    try:
        from server.app.services.runtime_profile.counters import profile

        return sum(int(pool.pending_depth()) for pool in profile.enqueue_pools)
    except Exception:
        # #204 broad-except audit: same gauge-reader contract as above —
        # the dispatch is registered by the worker thread and may not exist
        # in test/embedded shapes; absence reads as depth 0.
        return 0


def _fetch_one(conn: DatabaseConnection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    row = conn.execute(sql, params).fetchone()
    assert row is not None  # aggregate queries always return one row
    return row


class OpsMetricsService:
    def __init__(self, database_dsn: ConnectSource, config: dict[str, Any]) -> None:
        # database_dsn: JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187).
        self._database_dsn = database_dsn
        monitoring = config.get("monitoring", {})
        self._sample_interval_seconds = float(
            monitoring.get("sample_interval_seconds", _DEFAULT_SAMPLE_INTERVAL_SECONDS)
        )
        self._retention_days = int(monitoring.get("retention_days", _DEFAULT_RETENTION_DAYS))
        # Short-TTL summary cache for UI polling; see ops_metrics.summary.
        self._summary_cache: dict[tuple[str, str], tuple[datetime, dict[str, Any]]] = {}

    @property
    def sample_interval_seconds(self) -> float:
        return self._sample_interval_seconds

    @property
    def database_dsn(self) -> ConnectSource:
        """Read accessor for co-sampling surfaces (runtime profile, #359)."""
        return self._database_dsn

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

        The same pass also persists one runtime-profile gauge row (#359):
        the six-stage pipeline counters are process deltas over the bucket,
        the depth gauges reuse the queries already run here (queued,
        active_executions) plus the enqueue pool backlog, and the DB-pool
        wait gauges come from the psycopg pool stats.
        """
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
            online_worker_ids = {
                row["worker_id"]
                for row in conn.execute(
                    "select worker_id from agent_workers"
                    " where revoked_at is null and last_seen_at >= %s",
                    (online_since,),
                ).fetchall()
            }
            active_executions = _fetch_one(
                conn,
                "select count(*) as c from agent_execution_requests where state = 'claimed'",
            )["c"]
            # Queue depth is workspace-dimensioned: sampled on the global row
            # only, per-Worker rows keep the default 0.
            queued = _fetch_one(
                conn,
                "select count(*) as c from agent_execution_requests where state = 'queued'",
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
        self._sample_runtime_profile(bucket_start, queued=int(queued), active=active_executions)

    def _sample_runtime_profile(self, bucket_start: datetime, *, queued: int, active: int) -> None:
        """Persist the #359 runtime-profile gauge row; failures degrade to a
        missing profile row (the ops series must not depend on it)."""
        try:
            from server.app.services.runtime_profile import persist_profile_sample, profile

            pool_waiting, pool_wait_seconds = _db_pool_wait_gauges(self._database_dsn)
            persist_profile_sample(
                self._database_dsn,
                bucket_start,
                profile,
                queued_depth=queued,
                active_executions=active,
                enqueue_pending=_enqueue_pending_depth(),
                db_pool_waiting=pool_waiting,
                db_pool_wait_seconds=pool_wait_seconds,
            )
        except Exception:
            # #204 broad-except audit: metrics-side best-effort. The profile
            # row shares the ops sampling transaction boundary but not its
            # success contract: a missing profile bucket only thins the
            # series (the classifier reads whatever rows exist), while
            # letting this raise would kill the ops samples that already
            # committed in the caller and abort the sampling loop's
            # catch-up. The outcome space is the profile write surface plus
            # the pool-stat readers (attribute shapes differ across
            # psycopg-pool versions); logger.exception keeps the traceback
            # so a persistent failure is visible in logs.
            logger.exception("runtime profile sample failed for bucket %s", bucket_start)

    def sample_catch_up(self, now: datetime | None = None) -> int:
        """Persist every missing minute bucket since the last written sample."""
        return _sample_catch_up(self, now)

    def cleanup_expired(self, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now(UTC)) - timedelta(days=self._retention_days)
        with write_transaction(self._database_dsn) as conn:
            result = conn.execute(
                "delete from ops_metric_samples where bucket_start < %s", (cutoff,)
            )
        # Runtime-profile rows share the same retention window (#359), via the
        # JobQueries facade (BOUNDARY-DATA-001; new SQL does not join the
        # grandfathered baseline of this file).
        from server.app.jobs.queries.runtime_profile import (
            runtime_profile_queries_from_dsn,
        )

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
