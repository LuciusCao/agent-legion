"""Per-Worker batch lease heartbeat (protocol v5, #352).

One daemon loop per machine replaces the per-execution heartbeat threads:
each tick aggregates every live lease (running executions and queued upload
tasks alike) and renews them in a single ``POST /api/agent-executions/
heartbeats``. The heartbeat write load stops scaling with the slot count and
scales with the machine count instead.

Mixed-fleet compatibility: a Host that predates the batch endpoint answers
404/405, and this loop permanently degrades to per-execution beats — the
identical HTTP traffic and semantics older Hosts see today, from the same
loop (the registry stays authoritative, so prune/quiesce keep working). The
Host keeps serving the single endpoint to older Workers unchanged.

Liveness semantics are shared with the single path (``lifecycle.py``): a
lease the Host reports lost (409 family) fires the caller's
``ownership_lost`` and stops being batched; the zombie stop (agent process
dead, unadopted) prunes the entry so the Host's orphan sweeper can reclaim
the lease; the final-result-report quiesce window pauses one lease's beats
without stopping the loop.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# Batch size guard, mirroring the Host contract limit
# (server/app/agent_broker/heartbeat_batch.py). A healthy Worker holds at
# most max_concurrency + max_code_concurrency leases (registration caps both
# at 1024), so a live batch stays far below this; exceeding it means a
# bookkeeping bug (prunes stopped working), and silently renewing a truncated
# prefix would let the tail's leases expire anyway. Refuse the beat loudly —
# the sweeper reclaims the leases, which is the honest outcome.
MAX_BATCH_HEARTBEATS = 256

# Lease TTL margin (#349 baseline: 90s TTL, 30s fleet interval). A batch
# period above this is clamped back: one slow retry must not be able to cost
# every lease on the machine.
_MAX_BATCH_INTERVAL_SECONDS = 45.0
_CLAMPED_BATCH_INTERVAL_SECONDS = 30.0


@dataclass
class _LeaseEntry:
    """One registered lease: identity plus the shared single-beat state."""

    execution_id: str
    lease_id: str
    ownership_lost: threading.Event
    on_cancelled: Callable[[list[str]], Any] | None = None
    proc_ref: dict[str, subprocess.Popen[bytes] | None] = field(
        default_factory=lambda: {"proc": None}
    )
    adopted: threading.Event = field(default_factory=threading.Event)
    # Beats paused (final result report in flight): a single beat racing the
    # commit logs a spurious "lost ownership" 409.
    quiesced: bool = False


class BatchHeartbeatRegistry:
    """Thread-safe registry of this Worker's live leases.

    ``register`` at claim time (or upload restore), ``prune`` when the lease
    is done, ``quiesce``/``resume`` around the final result report. The
    per-Worker coordinator thread turns the registry into one batched beat
    per interval. The executor-side facade (``execution/heartbeat.py``) and
    the upload-side helpers (``upload/heartbeat.py``) are the only callers.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, _LeaseEntry] = {}
        # Set once the Host turned out to predate the batch endpoint; the
        # loop then beats per execution from the same registry.
        self.degraded_to_single = False

    def register(
        self,
        execution_id: str,
        lease_id: str,
        ownership_lost: threading.Event,
        on_cancelled: Callable[[list[str]], Any] | None = None,
    ) -> _LeaseEntry:
        entry = _LeaseEntry(
            execution_id=execution_id,
            lease_id=lease_id,
            ownership_lost=ownership_lost,
            on_cancelled=on_cancelled,
        )
        with self._lock:
            self._entries[execution_id] = entry
        return entry

    def prune(self, execution_id: str) -> None:
        """Stop beating one lease (result delivered, ownership lost, discard)."""
        with self._lock:
            self._entries.pop(execution_id, None)

    def quiesce(self, execution_id: str) -> None:
        with self._lock:
            entry = self._entries.get(execution_id)
            if entry is not None:
                entry.quiesced = True

    def resume(self, execution_id: str) -> None:
        """Resume beats after a transient report failure backs off."""
        with self._lock:
            entry = self._entries.get(execution_id)
            if entry is not None:
                entry.quiesced = False

    def set_adopted(self, execution_id: str) -> None:
        """Upload adoption: beats must outlive the exited agent process."""
        with self._lock:
            entry = self._entries.get(execution_id)
            if entry is not None:
                entry.adopted.set()

    def snapshot(self) -> list[_LeaseEntry]:
        """Beatable entries: alive, not quiesced, not already lost.

        Entries whose agent process exited unadopted are pruned here (the
        per-execution loop's zombie stop): the Host's orphan sweeper must be
        able to reclaim that lease instead of the Worker hiding a zombie."""
        snapshot: list[_LeaseEntry] = []
        with self._lock:
            for execution_id, entry in list(self._entries.items()):
                proc = entry.proc_ref["proc"]
                if proc is not None and proc.poll() is not None and not entry.adopted.is_set():
                    del self._entries[execution_id]
                    print(
                        f"heartbeat stopping for {execution_id}: agent process exited unadopted",
                        flush=True,
                    )
                    continue
                if entry.quiesced or entry.ownership_lost.is_set():
                    continue
                snapshot.append(entry)
        return snapshot


def batch_heartbeat_loop(
    client: Any,
    registry: BatchHeartbeatRegistry,
    stop: threading.Event,
    interval: float,
) -> None:
    """Beat once per interval for every registered lease of this Worker.

    Batch-first: one request renews the whole snapshot. A Host without the
    batch endpoint (404/405) flips the registry to degraded mode and the
    loop beats per execution from then on — identical traffic and semantics
    to the pre-v5 Worker. Transient errors keep the loop alive (the next
    tick retries); the real deadline is the Host-side lease TTL, exactly as
    in the single-beat loop."""
    while not stop.wait(interval):
        entries = registry.snapshot()
        if not entries:
            continue
        if not registry.degraded_to_single and _beat_batch(client, registry, entries):
            continue
        _beat_single(client, entries)


def _beat_batch(client: Any, registry: BatchHeartbeatRegistry, entries: list[_LeaseEntry]) -> bool:
    """One batched beat; False when the Host predates the endpoint."""
    if len(entries) > MAX_BATCH_HEARTBEATS:
        # Bookkeeping bug, not a scale problem (see MAX_BATCH_HEARTBEATS):
        # refuse to renew a silent prefix — the sweeper reclaims everything.
        print(
            f"batch heartbeat over limit ({len(entries)} > {MAX_BATCH_HEARTBEATS});"
            " refusing to beat",
            flush=True,
        )
        return True
    try:
        outcome = client.heartbeat_batch(
            [(entry.execution_id, entry.lease_id) for entry in entries]
        )
    except Exception as exc:
        # #204 broad-except audit: 批量心跳线程的存活语义（与单条路径的
        # heartbeat_loop 同一语义钉子）。逃逸族（requests 传输错误、畸形应
        # 答、非 200 状态的 RuntimeError）逐拍独立——失败只意味着这一拍没
        # 送达，interval 后的下一拍重新证明存活，真正的死线是 Host 侧租
        # 约 TTL。吞掉并继续是对的：让一次失败杀死这个 daemon 线程反而让
        # 本机全部租约静默过期、执行被 Host 重调度。结果空间是这一批的
        # 丢拍，无状态残留。日志保全：每次失败都 print（flush=True），持
        # 续性故障按 interval 反复可见，不会静默。
        print(f"batch heartbeat error ({len(entries)} leases): {exc}", flush=True)
        return True
    if outcome is None:
        # 404/405: pre-v5 Host — degrade to per-execution beats for the life
        # of the process. Old Hosts see exactly the old Worker's traffic.
        print(
            "Host lacks the batch heartbeat endpoint; using per-execution heartbeats",
            flush=True,
        )
        registry.degraded_to_single = True
        return False
    _status, body = outcome
    lost = set(str(value) for value in body.get("lost", []))
    for entry in entries:
        if entry.execution_id in lost:
            print(f"heartbeat lost ownership for {entry.execution_id}: batch 409", flush=True)
            entry.ownership_lost.set()
    cancelled = [str(value) for value in body.get("cancelled_execution_ids", [])]
    if cancelled:
        # Every entry's callback is (or wraps) cancel_executions, which
        # matches the list against its own registry — one call per distinct
        # callback is equivalent to the old N-thread N-calls and idempotent.
        delivered: set[int] = set()
        for entry in entries:
            if entry.on_cancelled is None or id(entry.on_cancelled) in delivered:
                continue
            delivered.add(id(entry.on_cancelled))
            entry.on_cancelled(cancelled)
    return True


def _beat_single(client: Any, entries: list[_LeaseEntry]) -> None:
    """Degraded mode: one single-beat request per entry, old semantics."""
    for entry in entries:
        try:
            status, cancelled = client.heartbeat(entry.execution_id, entry.lease_id)
        except Exception as exc:
            # #204 broad-except audit: 同 _beat_batch 的逐拍存活语义，只是
            # 粒度回到单条——一次逃逸只丢这一拍的这一个租约，其余条目与本
            # 循环不受影响。日志保全：print 逐条记录。
            print(f"heartbeat error for {entry.execution_id}: {exc}", flush=True)
            continue
        if cancelled and entry.on_cancelled is not None:
            entry.on_cancelled(cancelled)
        if status in (401, 409):
            print(f"heartbeat lost ownership for {entry.execution_id}: HTTP {status}", flush=True)
            entry.ownership_lost.set()
        elif status not in (200, 204):
            print(
                f"heartbeat unexpected status for {entry.execution_id}: HTTP {status}", flush=True
            )


def clamp_batch_interval(interval: float) -> float:
    """Keep the batch period safely inside the lease-TTL margin (#352).

    The period inherits the existing ``heartbeat_interval_seconds`` config
    (15s default; the #349 fleet runs 30s). A batch beat is one request per
    machine per period, so a short period is nearly free now, and keeping
    the #349 margin (two lost beats tolerated against the 90s TTL) is what
    protects the whole machine's leases from one slow retry."""
    if interval <= 0:
        return 15.0
    if interval > _MAX_BATCH_INTERVAL_SECONDS:
        print(
            f"heartbeat_interval_seconds={interval} leaves too little lease-TTL margin;"
            f" clamping the batch heartbeat period to {_CLAMPED_BATCH_INTERVAL_SECONDS}s",
            flush=True,
        )
        return _CLAMPED_BATCH_INTERVAL_SECONDS
    return interval


def start_batch_heartbeat(
    client: Any, interval: float, stop: threading.Event
) -> BatchHeartbeatRegistry:
    """Start the per-Worker batch heartbeat daemon; returns its registry."""
    registry = BatchHeartbeatRegistry()
    thread = threading.Thread(
        target=batch_heartbeat_loop,
        args=(client, registry, stop, clamp_batch_interval(interval)),
        daemon=True,
    )
    thread.start()
    return registry
