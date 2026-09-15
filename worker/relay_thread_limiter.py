"""Cross-tick thread admission for heartbeat relay shard requests."""

from __future__ import annotations

import threading
from collections.abc import Callable

# Ceiling-scaled shard admission (#657 + codex review): the registry carries
# BOTH capacity planes — executing leases AND upload-queue-adopted leases
# (release_slot hands the lease to the upload lane, which keeps it beating
# until the result report commits). Each plane is independently bounded by
# MAX_DYNAMIC_CONCURRENCY, so a fully saturated snapshot reaches ~2 × 2048
# leases = 4096, i.e. ceil(2 × MAX_DYNAMIC_CONCURRENCY / RELAY_BEAT_SHARD)
# = 64 concurrent shards. Undersizing here starves the snapshot TAIL every
# tick (the limiter skips un-slotted shards as unknown-round) — the tail
# leases then miss beats until they expire. Importing relay_shards here
# would be circular (it imports this limiter), so the contract test
# re-derives and pins the arithmetic instead.
MAX_INFLIGHT_SHARDS = 64


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
