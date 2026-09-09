"""Execution-dir ownership for the Worker (#564): attempt mutex + lease marker.

Two layers against the dual-attempt race — the Host requeues an execution
whose lease it judged expired while the old attempt thread is still alive
locally, and the same Worker immediately re-claims it:

- ``execution_mutex`` is a per-execution_id in-process lock table;
  ``run_execution`` holds the lock for the whole attempt, so the old
  attempt's discard tail and the new attempt's prepare serialize instead of
  interleaving (the race that deleted a live prompt.md).
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


@dataclass
class _MutexEntry:
    lock: threading.Lock
    waiters: int


_TABLE_GUARD = threading.Lock()
_MUTEX_TABLE: dict[str, _MutexEntry] = {}


@contextmanager
def execution_mutex(execution_id: str) -> Iterator[None]:
    """Serialize attempts for one execution_id within this process.

    The table entry is dropped once the last holder/waiter leaves, so a
    long-lived Worker does not accumulate one lock per historical execution.
    """
    with _TABLE_GUARD:
        entry = _MUTEX_TABLE.get(execution_id)
        if entry is None:
            entry = _MutexEntry(lock=threading.Lock(), waiters=0)
            _MUTEX_TABLE[execution_id] = entry
        entry.waiters += 1
    with entry.lock:
        try:
            yield
        finally:
            with _TABLE_GUARD:
                entry.waiters -= 1
                if entry.waiters == 0:
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
