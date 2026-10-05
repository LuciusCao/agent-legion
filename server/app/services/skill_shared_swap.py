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

from server.app.services.skill_build_residue import is_build_residue
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

    #1038: build residue in the live dir is carried into the staged tree
    (``_carry_build_residue``) — "files omitted from the payload disappear"
    applies to authored files only, never to residue the export skipped.
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
                # #1038: carried under the lock, BEFORE the live dir moves —
                # a copy failure aborts the write with the live dir untouched.
                _carry_build_residue(shared_dir, staging)
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


def _carry_build_residue(shared_dir: Path, staging: Path) -> None:
    """Copy the live dir's build residue (``__pycache__/``, ``*.pyc``) into
    the staged tree so the swap does not remove it (#1038).

    Why keep rather than clean: the editing snapshot SKIPS residue, so a
    round-trip (export → PUT) never mentioned it — deleting it would turn
    "skipped from the export" into "deleted by omission", and the swap
    would silently remove files nobody asked to remove (a locally running
    validator also just recreates them). Residue is carried only where its
    owning directory survives in the new state (a dropped ``scripts/x/``
    takes its ``__pycache__`` with it), symlinks are never followed or
    copied, and existing staged paths win (the payload cannot author
    residue anyway — ``validate_shared_put_payload`` rejects it).
    """
    for dirpath, dirnames, filenames in os.walk(shared_dir, followlinks=False):
        current = Path(dirpath)
        relative_dir = current.relative_to(shared_dir)
        for filename in filenames:
            relative = relative_dir / filename
            if not is_build_residue(relative.as_posix()):
                continue
            source = current / filename
            owner = relative.parent
            while owner.parts and is_build_residue(owner.as_posix()):
                owner = owner.parent
            target = staging / relative
            if (
                source.is_symlink()
                or not source.is_file()  # FIFOs/devices: never open them
                or not (staging / owner).is_dir()
                or target.exists()
            ):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target, follow_symlinks=False)
        # Never descend through a symlinked directory (os.walk lists it in
        # dirnames but, with followlinks=False, does not enter it).
        dirnames[:] = [name for name in dirnames if not (current / name).is_symlink()]
