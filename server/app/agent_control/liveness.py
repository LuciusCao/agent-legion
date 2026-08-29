"""Throttled last_seen_at writes for Agent Worker authentication.

authenticate() runs on EVERY Worker API call (an idle Worker polls claim
every few seconds), making one write transaction per call the hottest
single-statement write path on the Host. last_seen_at only feeds the 30s
online threshold (agent_workers.ONLINE_THRESHOLD_SECONDS), so the write is
throttled per worker with ample headroom under that threshold.
"""

from __future__ import annotations

from time import monotonic

from server.app.db.dialect import ConnectSource
from server.app.db.transaction import write_transaction

_WRITE_INTERVAL_SECONDS = 10.0


class WorkerLiveness:
    """Process-local, per-worker throttle for last_seen_at writes."""

    def __init__(self, interval_seconds: float = _WRITE_INTERVAL_SECONDS) -> None:
        self._interval_seconds = interval_seconds
        self._writes: dict[str, float] = {}

    def record_seen(self, database_dsn: ConnectSource, worker_id: str) -> None:
        """Write last_seen_at at most once per worker per interval.

        The registry is shared across threadpool workers, so two threads may
        both pass the throttle check and write twice — the update is
        idempotent, so no lock is taken."""
        now = monotonic()
        last_write = self._writes.get(worker_id)
        if last_write is not None and now - last_write < self._interval_seconds:
            return
        with write_transaction(database_dsn) as conn:
            conn.execute(
                "update agent_workers set last_seen_at=current_timestamp where worker_id=%s",
                (worker_id,),
            )
        self._writes[worker_id] = now

    def discard(self, worker_id: str) -> None:
        """Drop the memo entry for a revoked worker so the dict stays bounded."""
        self._writes.pop(worker_id, None)
