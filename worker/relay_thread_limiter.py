"""Cross-tick thread admission for heartbeat relay shard requests."""

from __future__ import annotations

import threading
from collections.abc import Callable

# Ceiling-scaled shard admission (#657 + codex review rounds): the registry
# carries leases from every live capacity plane — the agent and code
# EXECUTING pools (each independently bounded by MAX_DYNAMIC_CONCURRENCY)
# and the upload lane's adopted leases (release_slot hands the lease over;
# it keeps beating until the result report commits; the claim backpressure
# gate allows the upload backlog to reach ~2× the pool capacity). The
# worst-case snapshot is therefore ~(2 + 2) × MAX_DYNAMIC_CONCURRENCY
# leases = 8192 = 128 shards at RELAY_BEAT_SHARD leases each. The upload
# plane's 4096 share is ENFORCED, not assumed: load_transfer_controls caps
# upload_backlog_limit at 2×MAX_DYNAMIC_CONCURRENCY (raising the cap, the
# ceiling, or this constant requires all three plus the ceiling contract
# test to move together — each side names the others). Before that cap the
# overshoot failure mode was persistent tail starvation to lease expiry
# (registry insertion order is stable across ticks, so skipped tail shards
# are the SAME tail every tick — NOT a next-tick retry), the codex #662
# review P1. Undersizing starves the snapshot TAIL every tick. Oversizing
# costs idle daemon threads in the by-design-idle supervisor process.
# Importing relay_shards here would be circular (it imports this limiter),
# so the contract test re-derives the bound.
MAX_INFLIGHT_SHARDS = 128


class ShardThreadLimiter:
    """Bound request threads across relay ticks, not just within one beat.

    Fairness cursor (codex #662 review): overstaying sockets keep their
    slots across ticks, so a saturated snapshot may admit fewer shards
    than MAX_INFLIGHT_SHARDS. ``note_skip`` records the stable lease
    identities in each shard the tick could not start, in admission order.
    ``take_rotation`` resolves the first identity that still exists in the
    next fresh snapshot and returns its current shard index. This makes the
    first skipped live shard the next head without assuming snapshot indices
    survive completions, pruning, or newly appended leases.

    Every lease identity from the skipped suffix is retained for one tick,
    rather than only the first lease of the first shard: if that lease (or
    the whole first skipped shard) settles between snapshots, resolution
    advances to the next surviving member or shard. A clean tick records no
    anchors, so the following tick naturally starts from the head.
    """

    def __init__(self, max_inflight: int = MAX_INFLIGHT_SHARDS) -> None:
        self._slots = threading.BoundedSemaphore(max_inflight)
        self._lock = threading.Lock()
        self._skipped_anchors: list[tuple[str, str]] = []

    def note_skip(self, shard: list[tuple[str, str]]) -> None:
        """Remember a skipped shard by stable lease identity.

        The caller visits shards in admission order. Keeping the whole shard
        gives the next tick a fallback when its leading lease settles before
        the fresh snapshot is published.
        """
        with self._lock:
            self._skipped_anchors.extend(shard)

    def take_rotation(self, shards: list[list[tuple[str, str]]]) -> int:
        """Resolve the previous tick's first live skipped lease in ``shards``.

        Anchors are consumed once. Snapshot churn can move an anchor to a
        different numeric shard; matching identity before returning the
        current index is what preserves fairness across that churn.
        """
        with self._lock:
            anchors = self._skipped_anchors
            self._skipped_anchors = []
        if not anchors or len(shards) < 2:
            return 0
        shard_by_lease = {
            lease: shard_index for shard_index, shard in enumerate(shards) for lease in shard
        }
        for anchor in anchors:
            if (shard_index := shard_by_lease.get(anchor)) is not None:
                return shard_index
        return 0

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
