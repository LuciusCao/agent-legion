"""Work-root hygiene for the Agent Worker supervisor.

Kept separate from worker/executor.py to respect that file's size
ceiling; the executor re-exports these helpers. Stale-execution
sweeping lives in worker/stale_sweep.py.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from worker.upload_queue import PENDING_FILENAME


def clean_work_root(work_root: Path) -> None:
    """Drop execution dirs left behind by a crashed supervisor.

    Dirs holding an upload_pending.json marker contain results the
    UploadQueue has just restored for delivery; they are kept until their
    report resolves."""
    work_root.mkdir(parents=True, exist_ok=True)
    for child in work_root.iterdir():
        # is_dir() 跟随 symlink：对 symlink 只能 unlink，rmtree 会报错或误伤目标。
        if child.is_symlink():
            child.unlink()
        elif child.is_dir() and not (child / PENDING_FILENAME).is_file():
            shutil.rmtree(child, ignore_errors=True)
