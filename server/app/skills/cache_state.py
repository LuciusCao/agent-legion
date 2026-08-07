"""Read-only cache state probes for the skill manager (issue #42).

The skills base dir is a read-only input (external repo, ro-mounted in
container deployments), so the runtime path must not write to it: when the
cache worktree already sits clean at the pinned commit, ``checkout -f`` /
``clean -fd`` are skipped entirely.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

GitRunner = Callable[..., subprocess.CompletedProcess[str]]


def cache_at_commit(run_git: GitRunner, cache_dir: Path, commit: str) -> bool:
    """True when the cache worktree is clean and already at ``commit``.

    Both probes are read-only git calls, so a clean, pinned cache works even
    on a read-only skills mount. A dirty worktree (stray files, edits) misses
    the fast path and falls back to the full checkout/clean path.
    """
    head = run_git(["-C", str(cache_dir), "rev-parse", "HEAD^{commit}"]).stdout.strip()
    if head != commit:
        return False
    status = run_git(["-C", str(cache_dir), "status", "--porcelain"])
    return status.stdout.strip() == ""
