"""Execution-copy materialization for the skill manager (#330).

``export_commit`` extracts a commit's tree via ``git archive``: pure
object-database reads, ZERO working-tree writes against the user's
authoritative in-place repo — the retired ``checkout -f`` / ``clean -fd``
chain destroyed uncommitted work and left detached HEADs behind. Execution
content is exactly the commit's tree: uncommitted edits and untracked files
are ignored, never deleted. Split from ``manager.py`` for the file budget.
"""

from __future__ import annotations

import shutil
import subprocess
import tarfile
from collections.abc import Callable
from pathlib import Path

from server.app.skills.paths import ensure_secure_runs_dir

GitRunner = Callable[..., subprocess.CompletedProcess[str]]


def export_commit(
    run_git: GitRunner, runs_dir: Path, cache_dir: Path, commit: str, run_dir: Path
) -> None:
    """Export ``commit``'s tree into ``run_dir`` (replaced when present)."""
    if run_dir.exists():
        shutil.rmtree(run_dir)
    # Secure-root first use: never mkdir into a pre-created or
    # symlinked runs dir on a shared temp filesystem.
    ensure_secure_runs_dir(runs_dir)
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    archive_path = run_dir.parent / f"{run_dir.name}.tar"
    try:
        run_git(["-C", str(cache_dir), "archive", "--format=tar", "-o", str(archive_path), commit])
        # The repo is local and platform-managed; filter="data" is the
        # hardened default regardless (path/link scrubbing on extract).
        with tarfile.open(archive_path) as tar:
            tar.extractall(run_dir, filter="data")
    finally:
        archive_path.unlink(missing_ok=True)
