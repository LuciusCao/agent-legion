"""Lease-aware filesystem cleanup for completed or moot upload tasks."""

from __future__ import annotations

import json
import shutil

from worker.execution.ownership import discard_owned_dir
from worker.upload.constants import PENDING_FILENAME
from worker.upload.task import UploadTask


def drop_marker(task: UploadTask) -> bool:
    """Remove this lease's marker and owned directory after a final verdict.

    The UploadHandoff barrier prevents a new local attempt from touching this
    path until finalization signals ``delivery_done``. Marker lease validation
    remains a fail-closed defense for unexpected external writers. Missing or
    corrupt markers are removable orphans; the owner marker still decides
    whether the directory itself may be deleted.
    """
    marker = task.execution_dir / PENDING_FILENAME
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    owned = str(payload.get("lease_id") or "")
    if owned and owned != str(task.lease_id):
        print(f"keeping pending marker for {task.execution_id}: owned by {owned!r}", flush=True)
        return False
    marker.unlink(missing_ok=True)
    if not discard_owned_dir(task.execution_dir, task.lease_id):
        return False
    shutil.rmtree(task.execution_dir, ignore_errors=True)
    return True
