"""Disk/git side of the build-residue rule (#1038).

Split from ``skill_save_guards`` / ``skill_shared_swap`` for their file
budgets: the dirty-tree classification that exempts UNSTAGED residue, and
the ``_shared`` swap's carry-over of residue the editing export skipped.
The pure name rule lives in ``skill_build_residue``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from server.app.services.skill_build_residue import is_build_residue

GitRunner = Callable[..., subprocess.CompletedProcess[str]]


def tree_blocks_save(run_git: GitRunner, repo_dir: Path) -> bool:
    """Whether the repo's working tree / index is dirty beyond UNSTAGED
    build residue (see ``skill_save_guards.check_clean`` for the policy).

    ``-z`` prints raw (unquoted) path bytes: a non-UTF-8 file name (Linux)
    fails the runner's strict text decode. Such an entry is dirt we cannot
    classify — it blocks like any dirty tree (the caller's 409, never a 500).
    """
    try:
        status = run_git(
            repo_dir, ["status", "--porcelain", "-z", "--untracked-files=all"], check=False
        )
        return status.returncode != 0 or bool(blocking_entries(status.stdout))
    except UnicodeDecodeError:
        return True


def blocking_entries(porcelain_z: str) -> list[str]:
    """Dirty entries of ``git status --porcelain -z`` that block a save."""
    blocking: list[str] = []
    tokens = porcelain_z.split("\0")
    index = 0
    while index < len(tokens):
        entry = tokens[index]
        index += 1
        if not entry:
            continue
        code, path = entry[:2], entry[3:]
        if "R" in code or "C" in code:
            index += 1  # the rename/copy source path follows as its own token
            blocking.append(path)
            continue
        if code[0] in " ?" and is_build_residue(path):
            continue
        blocking.append(path)
    return blocking


def carry_build_residue(shared_dir: Path, staging: Path) -> None:
    """Copy the live dir's build residue (``__pycache__/``, ``*.pyc``) into
    the staged tree so the ``_shared`` swap does not remove it.

    Why keep rather than clean: the editing snapshot SKIPS residue, so a
    round-trip (export → PUT) never mentioned it — dropping it would turn
    "skipped from the export" into "deleted by omission", and the swap
    would silently remove files nobody asked to remove (a locally running
    validator also just recreates them). Residue is carried only where its
    owning directory survives in the new state (a dropped ``scripts/x/``
    takes its ``__pycache__`` with it), symlinks are never followed or
    copied, and existing staged paths win (the payload cannot author
    residue anyway — ``validate_shared_put_payload`` rejects it). A file a
    validator outside the lock removes between the walk and the copy is
    skipped; any other OSError propagates and aborts the write.
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
            try:
                shutil.copy2(source, target, follow_symlinks=False)
            except FileNotFoundError:
                target.unlink(missing_ok=True)
        # Never descend through a symlinked directory (os.walk lists it in
        # dirnames but, with followlinks=False, does not enter it).
        dirnames[:] = [name for name in dirnames if not (current / name).is_symlink()]
