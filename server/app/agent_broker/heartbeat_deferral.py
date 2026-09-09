"""Host-side stop-gap for Worker heartbeat starvation (#566).

An overloaded Worker can starve its in-process heartbeat daemon threads
(they lose the GIL) while its control plane keeps polling claims, so
``agent_workers.last_seen_at`` stays fresh — the execution plane is
starved, the Worker is not dead. Expiring those leases en masse feeds the
requeue → reclaim → overload → expire spiral. While the Worker's control
plane is fresh (the same online predicate as code_dispatch:
``last_seen_at`` younger than ``ONLINE_THRESHOLD_SECONDS``), the sweep
defers expiry and lets the next heartbeat renew the lease.

The deferral is bounded: silence longer than ``TTL + grace`` (grace = TTL,
so 2×TTL) expires as before — a truly dead attempt thread on a live Worker
must not hang forever.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from server.app.agent_control.registry import ONLINE_THRESHOLD_SECONDS

logger = logging.getLogger(__name__)

# One WARNING per execution per TTL bucket: a starved execution hits the
# deferral branch on every sweep until its heartbeat resumes or the hard
# cutoff passes, and per-sweep logging would flood the log during an
# overload episode. Entries are pruned to the currently-deferred set.
_log_buckets: dict[str, int] = {}


class HeartbeatDeferral:
    """Per-sweep decision helper for the fresh-control-plane deferral."""

    def __init__(self, conn: Any, ttl_seconds: int, rows: Iterable[dict[str, Any]]) -> None:
        self._ttl = ttl_seconds
        self._hard_cutoff = datetime.now(UTC) - timedelta(seconds=2 * ttl_seconds)
        self._fresh_workers = self._query_fresh_workers(conn, rows)
        self._deferred: set[str] = set()

    @staticmethod
    def _query_fresh_workers(conn: Any, rows: Iterable[dict[str, Any]]) -> set[str]:
        worker_ids = sorted({str(row["worker_id"]) for row in rows})
        if not worker_ids:
            return set()
        found = conn.execute(
            "select worker_id from agent_workers where worker_id = any(%s)"
            " and revoked_at is null"
            " and last_seen_at > now() - make_interval(secs => %s)",
            (worker_ids, ONLINE_THRESHOLD_SECONDS),
        ).fetchall()
        return {str(row["worker_id"]) for row in found}

    def should_defer(self, row: dict[str, Any]) -> bool:
        """True while the claim's Worker still polls claims and the heartbeat
        silence is inside the grace window; emits a throttled WARNING."""
        if str(row["worker_id"]) not in self._fresh_workers:
            return False
        heartbeat_at = _as_utc(row["heartbeat_at"])
        if heartbeat_at < self._hard_cutoff:
            return False
        execution_id = str(row["execution_id"])
        self._deferred.add(execution_id)
        silence = (datetime.now(UTC) - heartbeat_at).total_seconds()
        bucket = int(silence // self._ttl)
        if _log_buckets.get(execution_id) != bucket:
            _log_buckets[execution_id] = bucket
            logger.warning(
                "deferring expired agent lease %s exec=%s worker=%s: worker control"
                " plane fresh, heartbeat silent %.0fs (ttl=%ds, expires after %ds)",
                row["lease_id"],
                execution_id,
                row["worker_id"],
                silence,
                self._ttl,
                2 * self._ttl,
            )
        return True

    def prune_log_buckets(self) -> None:
        """Forget executions that left the deferral branch (expired or
        heartbeated), so a later starvation episode logs from scratch."""
        for key in [key for key in _log_buckets if key not in self._deferred]:
            del _log_buckets[key]


def _as_utc(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
