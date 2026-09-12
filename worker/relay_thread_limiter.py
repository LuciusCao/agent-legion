"""Cross-tick thread admission for heartbeat relay shard requests."""

from __future__ import annotations

import threading
from collections.abc import Callable

# A worker admits at most 1024 concurrent executions, so one healthy relay
# snapshot needs at most 16 shards. An overstaying socket keeps its slot until
# the request really returns, preventing a slow Host from creating an unbounded
# new wave of threads and sockets every relay interval.
MAX_INFLIGHT_SHARDS = 16


class ShardThreadLimiter:
    """Bound request threads across relay ticks, not just within one beat."""

    def __init__(self, max_inflight: int = MAX_INFLIGHT_SHARDS) -> None:
        self._slots = threading.BoundedSemaphore(max_inflight)

    def start(self, target: Callable[[], None]) -> threading.Thread | None:
        """Start ``target`` when a slot is free; release only on real exit."""
        if not self._slots.acquire(blocking=False):
            return None

        def run() -> None:
            try:
                target()
            finally:
                self._slots.release()

        thread = threading.Thread(target=run, daemon=True)
        try:
            thread.start()
        except RuntimeError:
            self._slots.release()
            raise
        return thread


__all__ = ["MAX_INFLIGHT_SHARDS", "ShardThreadLimiter"]
