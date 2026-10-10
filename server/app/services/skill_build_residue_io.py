"""Disk/git side of the build-residue rule (#1038).

Split from ``skill_save_guards`` / ``skill_shared_swap`` for their file
budgets: the dirty-tree classification that exempts UNSTAGED residue, and
the ``_shared`` swap's carry-over of residue the editing export skipped.
The pure name rule lives in ``skill_build_residue``.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
import subprocess
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path, PurePosixPath

from server.app.fs_safety import NotRegularFileError, open_regular_at
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


def carry_build_residue(shared_fd: int, staging: Path) -> None:
    """Copy the live dir's build residue (``__pycache__/``, ``*.pyc``) into
    the staged tree so the ``_shared`` swap does not remove it.

    Why keep rather than clean: the editing snapshot SKIPS residue, so a
    round-trip (export → PUT) never mentioned it — dropping it would turn
    "skipped from the export" into "deleted by omission", and the swap
    would silently remove files nobody asked to remove (a locally running
    validator also just recreates them). Residue is carried only where its
    owning directory survives in the new state (a dropped ``scripts/x/``
    takes its ``__pycache__`` with it), and existing staged paths win (the
    payload cannot author residue anyway — ``validate_shared_put_payload``
    rejects it). A file a validator outside the lock removes between the
    walk and the copy is skipped; any other OSError propagates and aborts
    the write.

    #1097: the walk runs on ``shared_fd`` — the identity-checked live dir
    the swap pinned — never on a path, so a ``_shared`` swapped for a
    symlink or another directory mid-walk cannot redirect the reads.
    ``os.fwalk(follow_symlinks=False)`` never enters a symlinked directory,
    and every source is ``lstat``-ed and opened ``O_NOFOLLOW`` relative to
    its directory descriptor: only single-link regular files are copied —
    symlinks, FIFOs, devices and hard links (which would alias a file from
    outside the tree) are skipped, also when swapped in after the lstat.
    """
    walk = os.fwalk(".", dir_fd=shared_fd, follow_symlinks=False)
    for dirpath, _dirnames, filenames, dir_fd in walk:
        relative_dir = PurePosixPath(dirpath)
        for filename in filenames:
            relative = relative_dir / filename
            if not is_build_residue(relative.as_posix()):
                continue
            owner = relative.parent
            while owner.parts and is_build_residue(owner.as_posix()):
                owner = owner.parent
            target = staging / relative
            if not (staging / owner).is_dir() or target.exists():
                continue
            with suppress(FileNotFoundError):  # removed by a validator outside the lock
                copy_residue_file(dir_fd, filename, target)


def copy_residue_file(dir_fd: int, name: str, target: Path) -> None:
    """Copy one residue file under ``dir_fd`` to a new ``target``, keeping
    its mode and timestamps like ``copy2``. Anything but a single-link
    regular file is skipped: ``lstat`` first so a FIFO/device is never
    opened, then ``open_regular_at`` (``O_NOFOLLOW``; ELOOP = swapped for a
    symlink after the lstat). A vanished source raises FileNotFoundError
    before ``target`` exists."""
    if not stat.S_ISREG(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        source = open_regular_at(dir_fd, name)
    except NotRegularFileError:
        return
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return
        raise
    with os.fdopen(source, "rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer)
        info = os.fstat(reader.fileno())
    os.chmod(target, stat.S_IMODE(info.st_mode))
    os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
