"""Leak GC for the skills runs dir (stale execution snapshots)."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from server.app.skills.paths import ensure_secure_runs_dir

logger = logging.getLogger(__name__)

# Execution snapshots live for seconds (copytree -> bundle -> finally-rmtree).
# An hour is two orders of magnitude above that, so anything older is debris
# from a hard crash (SIGKILL/OOM between copytree and the finally cleanup) —
# the OS temp-dir TTL still backstops anything this sweep somehow misses.
DEFAULT_MAX_AGE_SECONDS = 3600.0

_LOCKS_DIR_NAME = ".locks"


def sweep_stale_execution_dirs(runs_dir: Path, *, max_age_seconds: float) -> int:
    """Remove execution dirs under ``runs_dir`` older than ``max_age_seconds``.

    Called periodically by the sweeper thread. Age is judged by the
    execution dir's mtime; ``.locks`` and any non-directory entries (stray
    files, symlinked execution dirs) are never touched — the same
    escape-proofing ``SkillManager`` applies per execution id. Racy with a
    live dispatch only in the pathological case where a snapshot outlives
    the TTL it was supposed to finish within: ``get_skill_dir`` recreates
    the run dir, so a swept-but-in-use dir fails the copy loudly instead of
    silently corrupting results.
    """
    # The sweep is a deletion primitive: it must never run through a root
    # that fails the shared-temp trust rules (a symlinked or foreign-owned
    # runs dir would turn resolve() into an escape hatch for rmtree).
    # ensure_secure_runs_dir also creates the root on first sweep, so a
    # fresh install sweeps an empty 0700 dir instead of skipping.
    try:
        ensure_secure_runs_dir(runs_dir)
    except OSError:
        logger.exception("refusing to sweep skills runs dir %s", runs_dir)
        return 0
    root = runs_dir
    cutoff = time.time() - max_age_seconds
    swept = 0
    for entry in root.iterdir():
        if entry.name == _LOCKS_DIR_NAME or entry.is_symlink() or not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime > cutoff:
                continue
            shutil.rmtree(entry)
            swept += 1
            logger.warning("swept stale skill execution dir: %s", entry)
        except OSError:
            logger.exception("failed to sweep stale skill execution dir: %s", entry)
    return swept
