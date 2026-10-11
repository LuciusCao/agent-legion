"""Identity-checked directory moves and removal (#1097, PR #1242).

Built on ``fs_dir_identity`` (SECURITY-PATH-002): every primitive acts
relative to a pinned parent descriptor and refuses — or leaves alone — an
entry whose ``(st_dev, st_ino)`` is not the expected one. Renames act on
names, so each move re-checks right before and verifies right after; the
residual window between the two is documented per primitive.
"""

from __future__ import annotations

import os
from contextlib import suppress

from server.app.fs_dir_identity import (
    DirectoryIdentityError,
    DirIdentity,
    dir_identity_at,
    open_dir_identity_at,
    require_dir_identity_at,
)
from server.app.fs_safety import _component


def rename_at(parent: int, source: str, target: str) -> None:
    """Rename one entry to another name inside the same pinned directory."""
    os.rename(_component(source), _component(target), src_dir_fd=parent, dst_dir_fd=parent)


def retire_dir_at(parent: int, name: str, expected: DirIdentity, retired: str) -> None:
    """Rename ``name`` to ``retired`` under ``parent`` iff it is ``expected``.

    Re-checks the identity right before the rename and verifies the moved
    entry right after it. The rename itself acts on a name, so an entry
    swapped in between the two ``lstat``-s would move instead; that case is
    detected afterwards, moved back (best effort) and raised — on return
    ``retired`` is proven to hold ``expected``, on raise the caller owns
    nothing at ``retired`` and must not remove it.
    """
    require_dir_identity_at(parent, name, expected)
    rename_at(parent, name, retired)
    try:
        moved = dir_identity_at(parent, retired)
    except OSError:  # incl. DirectoryIdentityError: unverifiable = not ours
        moved = None
    if moved != expected:
        with suppress(OSError):
            rename_at(parent, retired, name)
        raise DirectoryIdentityError(f"{name!r} was swapped during the move")


def promote_dir_at(parent: int, source: str, expected: DirIdentity, target: str) -> None:
    """Rename ``source`` (proven ``expected``) onto an ABSENT ``target``.

    Checks before: ``source`` is still ``expected`` and ``target`` is free
    (a symlink/file there fails the lstat type check). Verifies after:
    ``target`` is ``expected``. POSIX ``rename`` cannot refuse an existing
    target, so an entry created in the window is replaced only if it is an
    EMPTY directory (a non-empty one fails ENOTEMPTY, a non-directory
    ENOTDIR) — nothing with content is ever lost; a source swapped in the
    window is caught by the post-check and raised (the caller restores or
    preserves its previous state).
    """
    require_dir_identity_at(parent, source, expected)
    if dir_identity_at(parent, target) is not None:
        raise DirectoryIdentityError(f"{target!r} appeared before the move")
    rename_at(parent, source, target)
    require_dir_identity_at(parent, target, expected)


def remove_tree_at(parent: int, name: str, expected: DirIdentity) -> bool:
    """Best-effort removal of directory ``name`` under ``parent``, only if it
    is ``expected``; returns whether it was removed.

    Contents are deleted through a descriptor pinned to ``expected``
    (bottom-up ``os.fwalk``, ``dir_fd``-relative unlink/rmdir, links
    removed, never followed); only the then-empty entry is removed by
    name. A directory swapped in under ``name`` meanwhile is therefore
    never emptied: ``rmdir`` fails on it (ENOTEMPTY / ENOTDIR) and it
    stays, unless it was itself empty. A missing or foreign entry, or any
    removal failure, leaves what is there and returns False.
    """
    try:
        _remove_pinned_tree(parent, name, expected)
    except OSError:
        return False
    return True


def _remove_pinned_tree(parent: int, name: str, expected: DirIdentity) -> None:
    with open_dir_identity_at(parent, name, expected) as fd:
        walk = os.fwalk(".", dir_fd=fd, topdown=False, follow_symlinks=False)
        for _dirpath, dirnames, filenames, dir_fd in walk:
            for entry in filenames:
                os.unlink(entry, dir_fd=dir_fd)
            for entry in dirnames:  # symlinked dirs are listed, not entered
                try:
                    os.rmdir(entry, dir_fd=dir_fd)
                except NotADirectoryError:
                    os.unlink(entry, dir_fd=dir_fd)
    os.rmdir(_component(name), dir_fd=parent)
