"""The full-state staged swap for the ``_shared`` materials (#633).

Extracted from ``skill_shared_store`` when the codex R4 double-failure
guard (preserve the retired copy when BOTH renames fail) grew the
function past that module's file budget. Lives with the write path it
implements: staging is pure file work, the swap runs under the shared
edit lock (imported from the store), and the retired-dir preservation
guarantees a failed-but-recoverable write never becomes data loss.

#1097 — identity, not paths, under the lock: the lock serializes our own
writers but cannot stop an out-of-band process from replacing ``_shared``
(or its parent) with a symlink or another directory. So the parent is
opened once (the trusted anchor, like ``fs_safety.open_dir_beneath``'s
root) and every later step is relative to that descriptor; ``_shared`` is
``lstat``-ed there (``fs_dir_identity``): absent → first write; a real
directory → its ``(st_dev, st_ino)`` is snapshotted; anything else
(symlink, file) → fail closed before any read. The residue carry walks a
descriptor opened ``O_NOFOLLOW`` and ``fstat``-matched to the snapshot,
and the retiring rename is re-checked right before and verified right
after (``retire_dir_at``). Residual window: the rename acts on a name, so
an entry swapped in between the last check and the rename is moved
instead — detected by the post-move check, moved back, the write fails
closed, and the cleanup never removes an unverified entry.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path

from server.app.fs_dir_identity import (
    dir_identity_at,
    open_dir_identity_at,
    rename_at,
    retire_dir_at,
)
from server.app.services.skill_build_residue_io import carry_build_residue
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
    (``skill_build_residue_io.carry_build_residue``) — "files omitted from
    the payload disappear" applies to authored files only, never to residue
    the export skipped.
    """
    staging_name = f"{SHARED_DIR_NAME}.tmp-{uuid.uuid4().hex[:12]}"
    retired_name = f"{SHARED_DIR_NAME}.old-{uuid.uuid4().hex[:12]}"
    staging = shared_dir.parent / staging_name
    parent_fd: int | None = None
    retired_owned = keep_retired = False
    try:
        for relative, content in files:
            path = staging / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        with shared_edit_lock(shared_dir, base_dir):
            # #1097: every step below is relative to the parent pinned here
            # and checked by lstat identity (see the module docstring). A
            # staging dir not under the pinned parent fails the promote
            # rename (ENOENT) — closed, like every other mismatch.
            parent_fd = os.open(shared_dir.parent, os.O_RDONLY | os.O_DIRECTORY)
            live = dir_identity_at(parent_fd, SHARED_DIR_NAME)  # symlink → fail closed
            if live is not None:
                # #1038: carried under the lock, BEFORE the live dir moves —
                # a copy failure aborts the write with the live dir untouched.
                with open_dir_identity_at(parent_fd, SHARED_DIR_NAME, live) as shared_fd:
                    carry_build_residue(shared_fd, staging)
                retire_dir_at(parent_fd, SHARED_DIR_NAME, live, retired_name)
                retired_owned = True
            try:
                rename_at(parent_fd, staging_name, SHARED_DIR_NAME)
            except OSError:
                if retired_owned:
                    # Restore; on a second failure `retired` is the ONLY copy
                    # of the previous state — keep it (see docstring) instead
                    # of turning a recoverable write into data loss.
                    try:
                        rename_at(parent_fd, retired_name, SHARED_DIR_NAME)
                    except OSError:
                        keep_retired = True
                        raise
                raise
    except OSError as exc:
        detail = f"shared materials write failed: {exc}"
        if keep_retired:
            retired = shared_dir.parent / retired_name
            detail += f"; the previous state is preserved at {retired} for manual recovery"
        raise SharedMaterialWriteError(detail) from exc
    finally:
        # Only a retired dir PROVEN to be the previous live dir is removed
        # (``retire_dir_at``); a restored one is already gone (no-op).
        if parent_fd is None:  # failed before the lock: nothing was retired
            shutil.rmtree(staging, ignore_errors=True)
        else:
            removable = [staging_name] + [retired_name] * (retired_owned and not keep_retired)
            for name in removable:
                shutil.rmtree(name, ignore_errors=True, dir_fd=parent_fd)
            os.close(parent_fd)
