"""Pending-upload marker ownership for Worker execution preparation (#203).

The two prepare paths (``worker.execution.prepare`` agent path and
``worker.code_runner`` code path) both stage a claimed execution into
``work_root/<execution_id>`` and must clear any leftover dir first — except
when the dir holds an ``upload_pending.json`` marker owned by *this* claim's
lease: that result is queued for delivery and the dir belongs to the upload
queue (same exemption as ``cleanup.py`` / ``stale_sweep.py``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from worker.upload.queue import PENDING_FILENAME, PendingUploadExists


def refuse_if_pending_upload(execution_dir: Path, claim: dict[str, Any]) -> None:
    """Raise when execution_dir is owned by *this claim's* pending upload.

    Ownership is the marker's ``lease_id`` (present since marker schema v1 —
    ``UploadTask.to_json`` persists the claim-time lease). A marker carrying
    a different lease is an orphan: its result can never be reported (the
    Host 409s any stale lease) and the claim that produced it is gone, so
    the current attempt must not sacrifice itself for it (#203 P1: every
    claim burns attempt+1 and the sweeper stops requeueing past
    requeue_limit — the last allowed attempt hitting a stale marker would
    fail the node outright). Unreadable markers are treated as orphans for
    the same reason: restore() already discarded unreadable ones at startup.
    """
    marker = execution_dir / PENDING_FILENAME
    if not marker.is_file():
        return
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return  # 孤儿（损坏）marker：见 docstring，让 claim 照常进行
    owned = str(payload.get("lease_id") or "")
    current = str(claim.get("lease_id") or "")
    if owned and owned == current:
        raise PendingUploadExists(
            f"execution dir for {claim.get('execution_id')} holds an undelivered"
            f" {PENDING_FILENAME} owned by this lease; owned by the upload queue,"
            " refusing to prepare"
        )
