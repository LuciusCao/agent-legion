"""The full-state staged swap for the ``_shared`` materials (#633).

Extracted from ``skill_shared_store`` when the codex R4 double-failure
guard (preserve the retired copy when BOTH renames fail) grew the
function past that module's file budget. Lives with the write path it
implements: staging is pure file work, the swap runs under the shared
edit lock (imported from the store), and the retired-dir preservation
guarantees a failed-but-recoverable write never becomes data loss.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path

from server.app.services.skill_shared_store import (
    SHARED_DIR_NAME,
    SharedMaterialWriteError,
    shared_edit_lock,
)


def write_shared_materials(
    shared_dir: Path, files: Sequence[tuple[str, str]], base_dir: Path
) -> None:
    """Apply the full-state write atomically: stage every file (relative
    posix path + content) in a sibling temp dir — any failure there
    leaves the live dir untouched — then swap under the shared lock with
    two same-filesystem renames; the replaced dir's retirement is the
    removal pass for dropped files.

    Double-failure guard (codex R4 P1): if BOTH the promote rename and
    the restore rename fail, the live dir is gone and ``retired`` holds
    the ONLY copy of the previous state — the cleanup must leave it on
    disk (a sibling ``.old-<uuid>`` dir) for manual recovery; the error
    message names it.
    """
    staging = shared_dir.parent / f"{SHARED_DIR_NAME}.tmp-{uuid.uuid4().hex[:12]}"
    retired = shared_dir.parent / f"{SHARED_DIR_NAME}.old-{uuid.uuid4().hex[:12]}"
    keep_retired = False
    try:
        for relative, content in files:
            path = staging / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        with shared_edit_lock(shared_dir, base_dir):
            had_previous = shared_dir.is_dir()
            if had_previous:
                os.rename(shared_dir, retired)
            try:
                os.rename(staging, shared_dir)
            except OSError:
                if had_previous:
                    # Restore; on a second failure `retired` is the ONLY copy
                    # of the previous state — keep it (see docstring) instead
                    # of turning a recoverable write into data loss.
                    try:
                        os.rename(retired, shared_dir)  # staging stays garbage
                    except OSError:
                        keep_retired = True
                        raise
                raise
    except OSError as exc:
        detail = f"shared materials write failed: {exc}"
        if keep_retired:
            detail += f"; the previous state is preserved at {retired} for manual recovery"
        raise SharedMaterialWriteError(detail) from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if not keep_retired:
            shutil.rmtree(retired, ignore_errors=True)
