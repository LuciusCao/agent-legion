"""Work-root hygiene for the Agent Worker supervisor.

Kept separate from worker/executor.py to respect that file's size
ceiling; the executor re-exports these helpers.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from worker.upload_queue import PENDING_FILENAME

STALE_EXECUTION_MAX_AGE_SECONDS = 24 * 3600
SWEEP_INTERVAL_SECONDS = 3600


def clean_work_root(work_root: Path) -> None:
    """Drop execution dirs left behind by a crashed supervisor.

    Dirs holding an upload_pending.json marker contain results the
    UploadQueue has just restored for delivery; they are kept until their
    report resolves."""
    work_root.mkdir(parents=True, exist_ok=True)
    for child in work_root.iterdir():
        if child.is_dir() and not (child / PENDING_FILENAME).is_file():
            shutil.rmtree(child, ignore_errors=True)


def sweep_stale_executions(
    work_root: Path,
    max_age_seconds: float = STALE_EXECUTION_MAX_AGE_SECONDS,
    *,
    now: float | None = None,
) -> None:
    """Remove execution dirs untouched for longer than max_age_seconds.

    Dirs with a pending-upload marker are skipped: they hold unreported
    results and the UploadQueue owns their lifecycle. Anything else this
    old is a leftover of a crashed run and holds no value (its events.jsonl
    alone can reach 100MB+).
    """
    now = time.time() if now is None else now
    try:
        children = list(work_root.iterdir())
    except OSError:
        return
    for child in children:
        if not child.is_dir() or (child / PENDING_FILENAME).is_file():
            continue
        try:
            stale = now - child.stat().st_mtime > max_age_seconds
        except OSError:
            continue
        if stale:
            shutil.rmtree(child, ignore_errors=True)
