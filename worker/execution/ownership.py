"""Execution-dir ownership for the Worker (#564): attempt mutex + lease marker.

Two layers against the dual-attempt race — the Host requeues an execution
whose lease it judged expired while the old attempt thread is still alive
locally, and the same Worker immediately re-claims it:

- ``execution_mutex`` is a per-execution_id in-process lock table;
  ``run_execution`` holds the lock for the whole attempt, so the old
  attempt's discard tail and the new attempt's prepare serialize instead of
  interleaving (the race that deleted a live prompt.md). The wait is
  bounded (``MUTEX_WAIT_BOUND_SECONDS``): a new attempt must not wait
  longer than its own lease has left to live.
- ``write_owner_marker`` tags the execution dir with the claiming lease at
  prepare time; the discard tail (``discard_owned_dir``) rmtree's only when
  the marker still names its own lease — the correctness floor for any path
  the in-process mutex does not cover.

A leftover marker from a crashed Worker names a lease no live attempt
holds; startup hygiene (``cleanup.clean_work_root``) and prepare's stale-dir
drop both remove such dirs wholesale, and a re-claim re-tags the dir, so a
stale marker never protects an orphan from cleanup.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worker._atomic import atomic_write
from worker.upload.queue import PENDING_FILENAME

# Sibling of upload_pending.json: which lease the execution dir belongs to.
OWNER_FILENAME = "execution_owner.json"

# Bound on waiting for a busy execution mutex (#564 P2). The Worker does not
# know the Host's actual lease TTL (the 90s baseline is a Host-side #349
# constant); 60s leaves two 30s batch-heartbeat beats inside that baseline —
# enough for the old attempt to receive its 409 and finish its teardown. If
# even that is not enough the heartbeat plane is still starved and this
# claim's lease is already dead or dying Host-side, so the caller abandons
# the claim (no prepare, no report, no heartbeat) and leaves the lease to
# expire for a Host requeue once the Worker recovers.
MUTEX_WAIT_BOUND_SECONDS = 60.0


@dataclass
class _MutexEntry:
    lock: threading.Lock
    refs: int  # current holder + waiters


_TABLE_GUARD = threading.Lock()
_MUTEX_TABLE: dict[str, _MutexEntry] = {}


@contextmanager
def execution_mutex(execution_id: str, timeout: float | None = None) -> Iterator[bool]:
    """Serialize attempts for one execution_id within this process.

    Yields True with the lock held; with ``timeout`` set, yields False when
    it elapses while another attempt still holds the lock — the caller must
    then abandon the claim untouched (no prepare, no report, no heartbeat).
    The table entry is dropped once the last holder/waiter leaves, so a
    long-lived Worker does not accumulate one lock per historical execution.
    """
    with _TABLE_GUARD:
        entry = _MUTEX_TABLE.get(execution_id)
        if entry is None:
            entry = _MutexEntry(lock=threading.Lock(), refs=0)
            _MUTEX_TABLE[execution_id] = entry
        entry.refs += 1
    # 自增之后的所有路径（acquire 超时/抛异常、holder 正常退出）统一走这个
    # finally 回收计数与表项——任何中途异常都不得泄漏表项。
    acquired = False
    try:
        acquired = entry.lock.acquire() if timeout is None else entry.lock.acquire(timeout=timeout)
        yield acquired
    finally:
        if acquired:
            entry.lock.release()
        with _TABLE_GUARD:
            entry.refs -= 1
            if entry.refs == 0:
                _MUTEX_TABLE.pop(execution_id, None)


def write_owner_marker(execution_dir: Path, claim: dict[str, Any]) -> None:
    """Tag execution_dir with the claiming lease (prepare-time, post-mkdir)."""
    payload = json.dumps(
        {
            "version": 1,
            "execution_id": str(claim.get("execution_id") or ""),
            "lease_id": str(claim.get("lease_id") or ""),
        }
    )
    atomic_write(execution_dir / OWNER_FILENAME, payload)


def discard_owned_dir(execution_dir: Path, lease_id: str) -> bool:
    """Whether the local-discard tail may rmtree execution_dir.

    Either veto means the dir is no longer this attempt's to delete: an
    upload-pending marker (the UploadQueue owns it until delivery, #203), or
    an owner marker naming another lease (a re-claimed attempt rebuilt the
    dir and is using it, #564). A missing or unreadable owner marker is not
    proof of ownership either — never rmtree what this attempt cannot prove
    is still its own; true orphans belong to the stale sweeper.
    """
    if (execution_dir / PENDING_FILENAME).is_file():
        return False
    try:
        payload = json.loads((execution_dir / OWNER_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    owned = str(payload.get("lease_id") or "")
    return bool(owned) and owned == str(lease_id)
