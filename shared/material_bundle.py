"""Bundle directory-tree assembly on top of the material cache (#156).

A material bundle is a folder uploaded as one run item: members live in the
content-addressed cache as regular single files (dedup intact), and this
module assembles the folder's directory tree from them. Both execution
sides share these rules (design §6.2):

- the bundle address is a content hash of the manifest (sorted
  ``member_address<TAB>relative_path`` lines), so the Host and the Worker
  derive the same cache location for an identical member set;
- the tree lands at ``{cache_root}/{address[:2]}/{address}/{relpath}`` — a
  directory where the single-file layout has a file — built in a unique
  sibling temp dir and atomically renamed into place, so concurrent
  assemblers never observe a partial tree;
- members are hard-linked (inode shared, no double space, eviction of the
  member file never breaks the tree); filesystems without hard links fall
  back to a copy.

Stdlib only (``shared`` house rule), same as ``shared/material_cache.py``.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import uuid
from collections.abc import Iterable
from pathlib import Path

from shared.material_cache import MaterializeError, cache_file_path


def bundle_address(entries: Iterable[tuple[str, str]]) -> str:
    """The deterministic content address for a bundle manifest.

    ``entries`` are ``(member_address, relative_path)`` pairs. Raises
    ``MaterializeError`` for an empty manifest, and for any entry carrying
    a control character (``\\n``/``\\t`` etc.): the line-joined hash input
    is only unambiguous when neither field can contain the separators —
    member paths are already rejected at creation time, this is the
    defense-in-depth check at the hashing boundary.
    """
    pairs = [(str(address), str(path)) for address, path in entries]
    if not pairs:
        raise MaterializeError("bundle manifest is empty")
    for address, path in pairs:
        if _has_control_char(address) or _has_control_char(path):
            raise MaterializeError(
                f"bundle manifest entry must not contain control characters: {path!r}"
            )
    lines = sorted(f"{address}\t{path}" for address, path in pairs)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _has_control_char(value: str) -> bool:
    return any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)


def _validate_relpath(path: str) -> None:
    segments = path.split("/")
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or _has_control_char(path)
        or any(segment in ("", ".", "..") for segment in segments)
    ):
        raise MaterializeError(f"bundle member path is not a safe relative path: {path!r}")


def assemble_bundle_tree(
    cache_root: Path,
    address: str,
    members: Iterable[tuple[Path, str]],
) -> Path:
    """Link cached member files into the bundle's directory tree.

    ``members`` are ``(member_cache_path, relative_path)`` pairs. An
    existing final dir is already complete (the address is a content hash
    of the manifest) and is returned as-is; a lost rename race discards the
    temp copy because the winner holds identical bytes.
    """
    final = cache_file_path(cache_root, address)
    if final.is_dir():
        # Refresh the LRU clock of the whole tree (best effort).
        with contextlib.suppress(OSError):
            for dirpath, _dirnames, filenames in os.walk(final):
                for name in filenames:
                    os.utime(Path(dirpath) / name)
        return final
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = final.parent / f".{final.name}.{os.getpid()}.{uuid.uuid4().hex}.part"
    try:
        for member_path, relpath in members:
            _validate_relpath(relpath)
            target = tmp_path / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(member_path, target)
            except OSError:
                shutil.copy2(member_path, target)
        try:
            os.replace(tmp_path, final)
        except OSError:
            # Another assembler won the race; its tree holds identical bytes.
            if not final.is_dir():
                raise
    except BaseException:
        shutil.rmtree(tmp_path, ignore_errors=True)
        raise
    return final
