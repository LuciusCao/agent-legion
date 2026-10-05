"""Shared path-containment primitives (SECURITY-PATH-002, #928).

Two tiers, pick by what the caller does with the path:

- **Descriptor tier** (``open_dir_beneath`` / ``open_dir_nofollow`` /
  ``open_regular_at``): openat-style walks modelled on the MCP staging reader
  (``server/app/mcp_server/local_files.py``). The trusted root is opened once;
  every component below it is opened relative to the previous directory
  descriptor with ``O_NOFOLLOW``, so a symlink anywhere in the untrusted part
  fails the open and a directory swapped after the walk cannot redirect I/O
  done through the pinned descriptor. Use it for I/O on trees an untrusted
  party can write.
- **Resolution tier** (``resolve_within``): resolve ``root / relative`` and
  require the result to stay under the resolved root. Symlinks that land back
  inside the root are accepted. It answers "which path inside the root does
  this name denote" and is the shared form of the historic
  ``resolve() + relative_to()`` checks; it is a check-then-use pattern, so it
  does not by itself protect later I/O against a concurrent directory swap.

``relative_parts`` is the lexical gate (non-empty, relative, no ``..``)
callers apply to untrusted names before either tier.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path, PurePath, PurePosixPath

DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
REGULAR_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


class PathEscapeError(ValueError):
    """A path is malformed for containment or resolves outside its root."""


class NotRegularFileError(ValueError):
    """The opened entry is not a single-link regular file."""


def relative_parts(path: str | PurePath) -> tuple[str, ...]:
    """Lexically validate an untrusted relative name and return its parts.

    Rejects empty names, absolute names and any ``..`` component. Strings are
    read as POSIX paths (the wire format of every caller).
    """
    pure = PurePosixPath(path) if isinstance(path, str) else path
    if not pure.parts or pure.is_absolute() or ".." in pure.parts:
        raise PathEscapeError("path must be relative without '..' components")
    return tuple(pure.parts)


def resolve_within(root: Path, relative: str | PurePath, *, allow_root: bool = False) -> Path:
    """Resolve ``root / relative`` and require it to stay under resolved ``root``.

    Non-strict resolution (missing leaves are fine). An absolute ``relative``
    replaces the root before resolution and is therefore contained only when
    it already points inside. The root itself is accepted only with
    ``allow_root``.
    """
    resolved_root = root.resolve()
    target = (resolved_root / relative).resolve()
    if target == resolved_root:
        if allow_root:
            return target
        raise PathEscapeError("path resolves to the containment root")
    if not target.is_relative_to(resolved_root):
        raise PathEscapeError("path escapes the containment root")
    return target


def _component(part: str) -> str:
    if not part or part in (".", "..") or "/" in part or "\x00" in part:
        raise PathEscapeError("invalid path component")
    return part


@contextmanager
def open_dir_beneath(
    root: Path | str, parts: Sequence[str], *, create: bool = False, mode: int = 0o700
) -> Iterator[int]:
    """Yield a descriptor for ``root/<parts...>`` walked without following links.

    ``root`` is the trusted anchor and is opened normally (a symlinked root is
    followed, matching how deployments mount data directories); each of
    ``parts`` is opened relative to its parent descriptor with ``O_NOFOLLOW``.
    With ``create`` missing components are created with ``mode`` first.
    """
    components = [_component(part) for part in parts]
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in components:
            if create:
                with suppress(FileExistsError):
                    os.mkdir(part, mode=mode, dir_fd=fd)
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


@contextmanager
def open_dir_nofollow(path: Path) -> Iterator[int]:
    """Yield a descriptor for absolute ``path`` with no component followed.

    Every ancestor from the filesystem root down is opened ``O_NOFOLLOW``, so
    the path must be spelled without symlinks at all.
    """
    if not path.is_absolute() or ".." in path.parts:
        raise PathEscapeError("path must be absolute without '..' components")
    with open_dir_beneath(path.anchor, path.parts[1:]) as fd:
        yield fd


def create_new_at(parent: int, name: str, mode: int = 0o600) -> int:
    """Create ``name`` under ``parent`` for writing; never reuse or follow.

    ``O_EXCL`` refuses an existing entry (file or dangling link), so a write
    can neither overwrite existing data nor be redirected through a link.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    return os.open(_component(name), flags, mode, dir_fd=parent)


def open_regular_at(parent: int, name: str) -> int:
    """Open ``name`` under directory descriptor ``parent`` for reading.

    The final component is not followed and the open never blocks on FIFOs;
    the descriptor is returned only for a regular file with exactly one link
    (a hard link would alias a file from outside the tree). Raises
    ``NotRegularFileError`` otherwise and ``OSError`` when the open fails.
    """
    fd = os.open(_component(name), REGULAR_FILE_FLAGS, dir_fd=parent)
    try:
        info = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise NotRegularFileError("not a regular file with a single link")
    return fd
