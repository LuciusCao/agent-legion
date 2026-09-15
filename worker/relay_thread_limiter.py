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
    than MAX_INFLIGHT_SHARDS. ``note_skip`` records the admission index
    of each shard the tick could NOT start — the FIRST one recorded wins
    (the caller walks shards in admission order, so first-recorded IS the
    earliest skipped); ``take_rotation`` returns where the next tick's
    admission should begin — that shard becomes the head. Under a
    persistent deficit of d slots this composes to full round-robin for
    every d < N: each tick's head is the previous tick's first skip, so
    no shard is skipped twice in a row and the worst wait before a
    skipped shard's next admission is N-1 ticks — the #662 review P1's
    starve-the-tail-to-lease-expiry cannot form. (Taking the MINIMUM skip
    instead — an earlier draft — fails when the skip set wraps past
    index 0: min() pins to 0 and the cursor 2-cycles, starving the
    mid-list shards for d > N/2; first-wins has no wrap special case.)

    Index contract (codex #662 follow-up round): ``note_skip`` receives
    ORIGINAL-snapshot shard indices — beat_sharded converts its
    rotated-list position before recording, because ``take_rotation``'s
    result is applied as an offset into the NEXT tick's fresh unrotated
    list. Feeding it rotated indices would misplace the cursor whenever a
    previous tick's rotation shifted the list.
    """

    def __init__(self, max_inflight: int = MAX_INFLIGHT_SHARDS) -> None:
        self._slots = threading.BoundedSemaphore(max_inflight)
        self._lock = threading.Lock()
        self._first_skip: int | None = None

    def note_skip(self, admission_index: int) -> None:
        """Record one shard the tick could not admit, by ORIGINAL-snapshot
        index (see the class docstring's index contract). The FIRST skip
        wins — later skips in the same tick do not move the cursor —
        because the caller records in admission order, so the first
        recorded is the earliest skipped and the fairest next head
        (minimum-instead would 2-cycle at wrap; see the class docstring).
        Caller holds the round's result lock; this only needs its own
        cursor lock for the cross-tick read in take_rotation."""
        with self._lock:
            if self._first_skip is None:
                self._first_skip = admission_index

    def take_rotation(self, shard_count: int) -> int:
        """The next tick's admission start: the first skipped shard's
        ORIGINAL-snapshot index, consumed once (a clean tick with no skips
        resets to the head; the caller applies it as an offset into the
        fresh unrotated shard list)."""
        with self._lock:
            skip = self._first_skip
            self._first_skip = None
        if skip is None or shard_count < 2:
            return 0
        return skip % shard_count

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
