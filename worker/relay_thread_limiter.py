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
# leases = 8192 = 128 shards at RELAY_BEAT_SHARD leases each. This assumes
# the DEFAULT backpressure (upload_backlog_limit unset → hard gate 2×
# pool): an operator-configured backlog above that raises the true worst
# case past 8192 — the failure mode stays bounded (un-slotted shards read
# as an unknown round and retry next tick), so the overshoot degrades to
# tail retries rather than lease loss. Undersizing starves the snapshot
# TAIL every tick. Oversizing costs idle daemon threads in the
# by-design-idle supervisor process. Importing relay_shards here would be
# circular (it imports this limiter), so the contract test re-derives the
# bound.
MAX_INFLIGHT_SHARDS = 128


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
