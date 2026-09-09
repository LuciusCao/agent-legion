"""Executor-side lease-relay sync: cadence, liveness watchdog, one call.

The executor half of the #566 phase-2 relay (the file format lives in
``lease_snapshot.py``, the beater in ``heartbeat_relay.py``). The claim loop
calls ``executor_relay_sync`` every pass; the ``RelaySyncState`` holder owns
the 2s throttle, the last applied beat-result seq, and the liveness
watchdog.

Liveness watchdog (PR #572 review P2-1): the relay rewrites the beat-result
file every tick (even with empty verdicts), so an advancing ``seq`` is its
liveness proof. While this executor holds leases and the seq stops advancing
beyond the threshold, the relay is dead or the supervisor hung — the leases
will silently expire and double-run on reclaim, so the operator gets one
WARNING per stall episode (re-armed when the seq advances). Pure
observability: no result semantics change.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from worker.lease_snapshot import RESULT_FILENAME, read_beat_result, write_snapshot

# The claim loop passes several times per second; the snapshot write (one
# small atomic file) is throttled to this.
RELAY_SYNC_INTERVAL_SECONDS = 2.0


def relay_watchdog_threshold(interval: float) -> float:
    """The stall bound: three relay periods, with a floor covering slow ticks."""
    return max(3.0 * interval, 60.0)


class RelayLivenessWatchdog:
    """Tracks the beat-result seq; warns once per stall episode."""

    def __init__(
        self, threshold_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._threshold = threshold_seconds
        self._clock = clock
        self._last_change = clock()
        self._warned = False

    def note(self, seq: int, *, seq_changed: bool, leases_pending: bool, log: Any) -> None:
        if seq_changed:
            self._last_change = self._clock()
            self._warned = False
            return
        if (
            leases_pending
            and not self._warned
            and self._clock() - self._last_change > self._threshold
        ):
            self._warned = True
            log(
                f"WARNING: 心跳 relay 超过 {self._threshold:.0f}s 无新拍（seq={seq}）——"
                "relay 死亡或 supervisor 挂起，租约将静默过期重排；查 supervisor 进程"
            )


class RelaySyncState:
    """The executor's relay-sync state: throttle, last seq, watchdog."""

    def __init__(self, interval: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._next_sync = 0.0
        self._clock = clock
        self.last_seq = -1
        self.watchdog = RelayLivenessWatchdog(relay_watchdog_threshold(interval), clock)

    def due(self) -> bool:
        now = self._clock()
        if now < self._next_sync:
            return False
        self._next_sync = now + RELAY_SYNC_INTERVAL_SECONDS
        return True


def executor_relay_sync(
    registry: Any,
    snapshot_path: Path,
    worker_id: str,
    token: str,
    state: RelaySyncState,
    *,
    log: Any = print,
) -> None:
    """One executor-side relay round (throttled by ``state.due()``): publish
    the beatable snapshot, feed the watchdog, apply any new beat result.
    Never raises into the claim loop — a failed round is retried next pass
    (the lease TTL is the real deadline)."""
    if not state.due():
        return
    try:
        leases = [(entry.execution_id, entry.lease_id) for entry in registry.snapshot()]
        write_snapshot(
            snapshot_path,
            worker_id=worker_id,
            token=token,
            pid=os.getpid(),
            leases=leases,
        )
        result = read_beat_result(snapshot_path.parent / RESULT_FILENAME)
        new_seq = state.last_seq if result is None else int(result["seq"])
        state.watchdog.note(
            new_seq,
            seq_changed=new_seq != state.last_seq,
            leases_pending=bool(leases),
            log=log,
        )
        if result is None or result["seq"] == state.last_seq:
            return
        registry.apply_beat_result(
            lost=[(str(pair[0]), str(pair[1])) for pair in result.get("lost", [])],
            cancelled=[str(value) for value in result.get("cancelled", [])],
        )
        state.last_seq = int(result["seq"])
    except Exception as exc:
        # #204 broad-except audit: relay 同步是 claim 主循环的旁路 I/O——
        # 磁盘错误/半写文件/畸形结果只丢这一轮，下一轮（2s 后）重试；让
        # 它逃逸会杀死整个 claim 循环（worker 停摆），而容错面已就位：
        # 快照停滞 60s 后 relay 停拍、租约按 TTL 过期重排。日志保全：
        # print 逐次记录。
        print(f"lease relay sync failed: {exc}", flush=True)
