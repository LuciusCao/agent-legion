"""Crash-safe JSON job-artifact writes: fsynced same-directory temp + os.replace.

Split into staging and swap so a caller can stage outside a transaction and
swap inside it after its guard passes (approval decisions, #929 / #963);
readers never observe a half-written artifact. The swap is durable (#975):
``replace_durable`` fsyncs the target directory after ``os.replace`` so the
new directory entry survives a host crash once the caller commits.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

# Dot-prefixed subdirectory of the job dir: same filesystem as ``target`` (so
# the swap stays atomic), yet never enumerated as an artifact — the root
# listing takes files only, the deep scan prunes dot-prefixed subtrees and the
# download whitelist rejects dot segments (job_artifact_names).
STAGING_DIR_NAME = ".json-staging"


def stage_json(target: Path, payload: dict[str, Any]) -> Path:
    """Write ``payload`` to an fsynced temp file in ``target``'s staging
    subdirectory (same filesystem, so ``os.replace`` onto ``target`` is
    atomic); a failed write leaves no temp file behind. The caller swaps or
    unlinks the result."""
    staging_dir = target.parent / STAGING_DIR_NAME
    staging_dir.mkdir(exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=staging_dir, prefix=f"{target.name}.")
    staged = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        # #204 broad-except audit: staging-file cleanup guard, not a
        # swallow — the bare raise re-raises the original verbatim. The
        # width is deliberate: an interrupt during a partial write must
        # also remove the temp file.
        staged.unlink(missing_ok=True)
        raise
    return staged


def replace_durable(staged: Path, target: Path) -> None:
    """``os.replace`` ``staged`` onto ``target``, then fsync ``target``'s
    directory so the swapped-in entry is on disk before the caller commits
    (#975: a power loss after the DB commit must not lose the artifact)."""
    os.replace(staged, target)
    descriptor = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
