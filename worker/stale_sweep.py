"""Stale-execution sweeping for the Agent Worker supervisor.

Split from worker/cleanup.py to keep both files within their size
budgets; worker/executor.py imports and re-exports these helpers.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from worker.upload_queue import PENDING_FILENAME

STALE_EXECUTION_MAX_AGE_SECONDS = 24 * 3600
SWEEP_INTERVAL_SECONDS = 3600


def _subtree_latest_mtime(root: Path, latest: float) -> float:
    """Fold the mtime of every entry under root into `latest`.

    Runtime writes land in nested dirs (job/runs/...), and directory
    mtimes do not propagate upward, so the top-level execution dir's
    mtime freezes once the bundle is extracted. Judging staleness by it
    alone would rmtree a still-running execution after
    STALE_EXECUTION_MAX_AGE_SECONDS, silently losing its events and
    artifacts. The trade-off is an hourly full-tree walk per surviving
    dir — cheap next to a lost 24h+ run. Unreadable entries are skipped.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        for name in (*dirnames, *filenames):
            try:
                # lstat 不跟随 symlink： dangling link 不跳过整个子树，
                # 也不把链接目标（可能在 work_root 外）的 mtime 算进来。
                latest = max(latest, (Path(dirpath) / name).lstat().st_mtime)
            except OSError:
                continue
    return latest


def sweep_stale_executions(
    work_root: Path,
    max_age_seconds: float = STALE_EXECUTION_MAX_AGE_SECONDS,
    *,
    now: float | None = None,
) -> None:
    """Remove execution dirs untouched for longer than max_age_seconds.

    "Untouched" is judged by the newest mtime anywhere in the dir's
    subtree, not the top-level dir's mtime (see _subtree_latest_mtime).

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
        # is_dir() 跟随 symlink：symlink 一律 unlink，绝不 rmtree 到链接目标。
        if child.is_symlink():
            child.unlink(missing_ok=True)
            continue
        if not child.is_dir() or (child / PENDING_FILENAME).is_file():
            continue
        try:
            latest = child.stat().st_mtime
        except OSError:
            continue
        stale = now - _subtree_latest_mtime(child, latest) > max_age_seconds
        if stale:
            shutil.rmtree(child, ignore_errors=True)
