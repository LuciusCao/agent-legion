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
from contextlib import contextmanager
from pathlib import Path

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


def create_dir_identity(path: Path) -> DirIdentity:
    """Create a NEW directory ``path`` (missing ancestors too) and return its
    identity, pinned at creation so later checks can tell it apart from a
    directory swapped in under the same name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        raise DirectoryIdentityError(f"{path.name!r} is not a real directory")
    return (info.st_dev, info.st_ino)


def require_dir_identity_at(parent: int, name: str, expected: DirIdentity) -> None:
    """Fail closed unless ``name`` under ``parent`` is still ``expected``."""
    if dir_identity_at(parent, name) != expected:
        raise DirectoryIdentityError(f"{name!r} changed identity since it was checked")


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
