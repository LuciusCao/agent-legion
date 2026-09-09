"""Executor → supervisor lease snapshot IPC (#566 phase 2).

The batch lease heartbeat moved out of the executor process: a saturated
executor starves its in-process heartbeat daemon of the GIL (#566 phase 1),
while the supervisor process is idle by design. The executor periodically
writes its beatable lease set to ``lease_snapshot.json`` (atomic, mode 600
— it carries the worker token so the relay can authenticate); the
supervisor-side relay (``heartbeat_relay.py``) beats those leases and
writes the Host's verdicts to ``lease_beat_result.json`` for the executor
to apply (lost → ownership_lost, cancelled → cancel callbacks).

Both files live in the worker state dir (same trust domain as the
plaintext register tokens). A stale snapshot means the executor's main
loop is not iterating — the relay stops beating so the Host reclaims those
leases on the normal TTL path instead of hiding a brain-dead executor.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from worker._atomic import atomic_write

SNAPSHOT_FILENAME = "lease_snapshot.json"
RESULT_FILENAME = "lease_beat_result.json"
# Supervisor sets this env var for the executor child; its presence switches
# the executor from the in-process beat loop to snapshot mode (a bare
# ``python -m worker.executor`` run keeps the legacy loop).
SNAPSHOT_ENV_VAR = "AGENT_WORKER_LEASE_SNAPSHOT"
# Comfortably below the 90s lease TTL: a stalled executor's leases expire on
# the normal path instead of being beaten forever from a frozen snapshot.
SNAPSHOT_STALE_SECONDS = 60.0


def write_snapshot(
    path: Path,
    *,
    worker_id: str,
    token: str,
    pid: int,
    leases: list[tuple[str, str]],
    now: float | None = None,
) -> None:
    """Publish the current beatable lease set (atomic replace, mode 600)."""
    payload = {
        "worker_id": worker_id,
        "token": token,
        "pid": pid,
        "updated_at": time.time() if now is None else now,
        "leases": [[execution_id, lease_id] for execution_id, lease_id in leases],
    }
    atomic_write(path, json.dumps(payload), mode=0o600)


def read_snapshot(path: Path) -> dict[str, Any] | None:
    """Parse the snapshot; a missing or corrupt file reads as None (skip)."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("leases"), list):
        return None
    return payload


def snapshot_stale(snapshot: dict[str, Any], *, now: float, stale_seconds: float) -> bool:
    """True when the executor stopped refreshing the snapshot (main loop
    stalled) — the relay must stop beating so leases expire on schedule."""
    try:
        updated_at = float(snapshot["updated_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return now - updated_at > stale_seconds


def write_beat_result(
    path: Path,
    *,
    seq: int,
    lost: list[tuple[str, str]],
    cancelled: list[str],
) -> None:
    """Publish one beat round's verdicts; ``seq`` lets the executor apply
    each result exactly once."""
    payload = {
        "seq": seq,
        "lost": [[execution_id, lease_id] for execution_id, lease_id in lost],
        "cancelled": list(cancelled),
    }
    atomic_write(path, json.dumps(payload), mode=0o600)


def read_beat_result(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("seq"), int):
        return None
    return payload


# Executor-side relay sync cadence: the claim loop passes several times per
# second; the snapshot write (one small atomic file) is throttled to this.
EXECUTOR_RELAY_SYNC_SECONDS = 2.0


def open_lease_channel(client: Any, interval: float, stop: Any) -> tuple[Any, Path | None]:
    """Registry + optional snapshot path for the executor's lease channel.

    Snapshot mode (the supervisor set ``SNAPSHOT_ENV_VAR``): no in-process
    beat thread — the supervisor-side relay beats from the snapshot file.
    Bare executor runs keep the legacy in-process batch heartbeat loop."""
    from worker.execution.heartbeat_batch import BatchHeartbeatRegistry, start_batch_heartbeat

    raw = os.environ.get(SNAPSHOT_ENV_VAR, "").strip()
    if raw:
        return BatchHeartbeatRegistry(), Path(raw)
    return start_batch_heartbeat(client, interval, stop), None


def executor_relay_sync(
    registry: Any,
    snapshot_path: Path,
    *,
    worker_id: str,
    token: str,
    last_result_seq: int,
) -> int:
    """One executor-side relay round (#566 phase 2): publish the beatable
    lease snapshot, then apply any new beat result. Returns the applied
    result seq (``last_result_seq`` when nothing new). Never raises into the
    claim loop: a failed write/read costs one round, retried next pass —
    the real deadline is the lease TTL."""
    try:
        write_snapshot(
            snapshot_path,
            worker_id=worker_id,
            token=token,
            pid=os.getpid(),
            leases=[(entry.execution_id, entry.lease_id) for entry in registry.snapshot()],
        )
        result = read_beat_result(snapshot_path.parent / RESULT_FILENAME)
        if result is None or result["seq"] == last_result_seq:
            return last_result_seq
        registry.apply_beat_result(
            lost=[(str(pair[0]), str(pair[1])) for pair in result.get("lost", [])],
            cancelled=[str(value) for value in result.get("cancelled", [])],
        )
        return int(result["seq"])
    except Exception as exc:
        # #204 broad-except audit: relay 同步是 claim 主循环的旁路 I/O——
        # 磁盘错误/半写文件/畸形结果只丢这一轮，下一轮（2s 后）重试；让
        # 它逃逸会杀死整个 claim 循环（worker 停摆），而容错面已就位：
        # 快照停滞 60s 后 relay 停拍、租约按 TTL 过期重排。日志保全：
        # print 逐次记录。
        print(f"lease relay sync failed: {exc}", flush=True)
        return last_result_seq
