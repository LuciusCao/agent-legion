"""One-minute ops sampling dispatch and best-effort runtime gauges."""

from __future__ import annotations

import logging
from datetime import datetime

from server.app.db.dialect import ConnectSource

logger = logging.getLogger("server.app.services.ops_metrics.service")

_DB_POOL_LAST_STATS: dict[str, int] = {}


def _db_pool_wait_gauges(database_dsn: ConnectSource) -> tuple[int, float]:
    """Per-bucket pool-wait deltas (best-effort; never raises)."""
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
        # #204 broad-except audit: a missing pool signal must not abort sampling.
        return 0, 0.0


def _enqueue_pending_depth() -> int:
    """Momentary combined enqueue backlog (0 when unwired)."""
    try:
        from server.app.services.runtime_profile.counters import profile

        return sum(int(pool.pending_depth()) for pool in profile.enqueue_pools)
    except Exception:
        # #204 broad-except audit: optional in-process gauge; absence reads as 0.
        return 0


def sample_runtime_profile(
    database_dsn: ConnectSource, bucket_start: datetime, *, queued: int, active: int
) -> None:
    """Persist runtime gauges without making ops sampling depend on them."""
    try:
        from server.app.services.runtime_profile import persist_profile_sample, profile

        pool_waiting, pool_wait_seconds = _db_pool_wait_gauges(database_dsn)
        persist_profile_sample(
            database_dsn,
            bucket_start,
            profile,
            queued_depth=queued,
            active_executions=active,
            enqueue_pending=_enqueue_pending_depth(),
            db_pool_waiting=pool_waiting,
            db_pool_wait_seconds=pool_wait_seconds,
        )
    except Exception:
        # #204 broad-except audit: profile rows are best-effort; a failure only
        # thins that series and must not abort the committed ops samples.
        logger.exception("runtime profile sample failed for bucket %s", bucket_start)
