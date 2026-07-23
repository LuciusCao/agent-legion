"""Work-root hygiene for the Agent Worker supervisor.

Kept separate from scripts/agent_worker.py to respect that file's size
ceiling; agent_worker re-exports these helpers.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

STALE_EXECUTION_MAX_AGE_SECONDS = 24 * 3600
SWEEP_INTERVAL_SECONDS = 3600


def clean_work_root(work_root: Path) -> None:
    """Drop execution dirs left behind by a crashed supervisor."""
    work_root.mkdir(parents=True, exist_ok=True)
    for child in work_root.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)


def sweep_stale_executions(
    work_root: Path,
    max_age_seconds: float = STALE_EXECUTION_MAX_AGE_SECONDS,
    *,
    now: float | None = None,
) -> None:
    """Remove execution dirs untouched for longer than max_age_seconds.

    Results are reported back to the host as soon as a run finishes, so a
    local execution dir this old is a leftover of a crashed run and holds no
    value (its events.jsonl alone can reach 100MB+).
    """
    now = time.time() if now is None else now
    try:
        children = list(work_root.iterdir())
    except OSError:
        return
    for child in children:
        if not child.is_dir():
            continue
        try:
            stale = now - child.stat().st_mtime > max_age_seconds
        except OSError:
            continue
        if stale:
            shutil.rmtree(child, ignore_errors=True)
