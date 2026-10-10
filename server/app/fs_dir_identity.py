"""Directory identity snapshots for check-then-move flows (#1097).

Companion to ``fs_safety``'s descriptor tier (SECURITY-PATH-002): where
``open_dir_beneath`` pins *where* I/O happens, these primitives pin *which*
directory a name still denotes between a check and a later move. An
identity is ``(st_dev, st_ino)`` of an ``lstat`` taken relative to a parent
directory descriptor — never through a symlink — and only a real directory
has one: a symlink, file or other entry under the name is refused, so a
caller cannot be redirected into a link target.

All failures are ``OSError`` (``DirectoryIdentityError``) so the
filesystem-failure handlers that already fail a write closed cover them.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress

from server.app.fs_safety import DIRECTORY_FLAGS, _component

DirIdentity = tuple[int, int]


class DirectoryIdentityError(OSError):
    """A name is not (or is no longer) the expected real directory."""


def dir_identity_at(parent: int, name: str) -> DirIdentity | None:
    """``lstat`` ``name`` under directory descriptor ``parent``.

    Returns ``None`` when nothing is there, the identity for a real
    directory, and raises ``DirectoryIdentityError`` for anything else
    (symlink — even one pointing at a directory — file, FIFO, ...).
    """
    try:
        info = os.stat(_component(name), dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        raise DirectoryIdentityError(f"{name!r} is not a real directory (symlink or other entry)")
    return (info.st_dev, info.st_ino)


def require_dir_identity_at(parent: int, name: str, expected: DirIdentity) -> None:
    """Fail closed unless ``name`` under ``parent`` is still ``expected``."""
    if dir_identity_at(parent, name) != expected:
        raise DirectoryIdentityError(f"{name!r} changed identity since it was checked")


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


def rename_at(parent: int, source: str, target: str) -> None:
    """Rename one entry to another name inside the same pinned directory."""
    os.rename(_component(source), _component(target), src_dir_fd=parent, dst_dir_fd=parent)


@contextmanager
def open_dir_identity_at(parent: int, name: str, expected: DirIdentity) -> Iterator[int]:
    """Yield a descriptor for ``name`` under ``parent`` proven to be ``expected``.

    Opened ``O_NOFOLLOW | O_DIRECTORY`` and re-checked with ``fstat``: I/O
    through the descriptor stays on the snapshotted directory even if the
    name is swapped afterwards.
    """
    fd = os.open(_component(name), DIRECTORY_FLAGS, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != expected:
            raise DirectoryIdentityError(f"{name!r} changed identity since it was checked")
        yield fd
    finally:
        os.close(fd)
