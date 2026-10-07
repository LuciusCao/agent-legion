"""Lossless authoring snapshots; display projections are not write payloads."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from server.app.services.skill_build_residue import binary_file_error, is_residue_name
from server.app.services.skill_repo import MAX_FILE_BYTES
from server.app.services.skill_repo_edit import SkillEditValidationError
from server.app.services.skill_shared_put import validate_shared_put_payload

MAX_TREE_ENTRIES = 10000


def load_map_json(shared_dir: Path) -> dict:
    try:
        parsed: dict = json.loads((shared_dir / "map.json").read_bytes())
        return parsed
    except (OSError, UnicodeError, ValueError) as exc:
        raise SkillEditValidationError(
            "Invalid shared materials map",
            [{"path": "map.json", "error": f"unreadable or malformed JSON: {exc}"}],
        ) from exc


def text_file(path: str, raw: bytes, *, for_edit: bool = False) -> dict[str, Any]:
    if for_edit:
        return edit_file(path, raw)
    return {
        "path": path,
        "size": len(raw),
        "content": raw[:MAX_FILE_BYTES].decode("utf-8", errors="replace"),
        "truncated": len(raw) > MAX_FILE_BYTES,
    }


def edit_file(path: str, raw: bytes, *, max_bytes: int = MAX_FILE_BYTES) -> dict[str, Any]:
    """Reject lossy decoding and truncation before producing an editable file."""
    try:
        if len(raw) > max_bytes:
            raise ValueError("file exceeds the editable byte limit")
        content = raw.decode("utf-8")
    except (UnicodeError, ValueError) as exc:
        # #1038：只报解码错误时调用方不知道该怎么办——点明这是二进制、
        # 只收 UTF-8 文本，并给出可执行的处置（构建残留本身已在遍历时跳过）。
        error = binary_file_error(exc) if isinstance(exc, UnicodeError) else str(exc)
        raise SkillEditValidationError(
            "Cannot export a lossless editing snapshot", [{"path": path, "error": error}]
        ) from exc
    return {"path": path, "size": len(raw), "content": content, "truncated": False}


def shared_edit_snapshot(root: Path) -> list[dict[str, Any]]:
    files = edit_tree(root)
    validate_shared_put_payload(root, [(item["path"], item["content"]) for item in files])
    return files


def edit_tree(root: Path) -> list[dict[str, Any]]:
    """Read every file, including raw map.json, under the caller's shared lock.

    Refuse unreadable/unsupported trees as a whole: omission from a FULL-state
    payload means deletion. Walk directory descriptors without following links.

    #1038: build residue (``__pycache__/``, ``*.pyc`` — ``skill_build_residue``)
    is skipped by NAME before any stat/open, so it never reaches the UTF-8
    check; the full-state write carries it over instead of deleting it
    (``skill_shared_swap``), so skipping it here is not an implied deletion.
    """
    files: list[dict[str, Any]] = []
    visited = 0

    def names(directory: int) -> list[str]:
        nonlocal visited
        result = []
        with os.scandir(directory) as entries:
            for entry in entries:
                visited += 1
                if visited > MAX_TREE_ENTRIES:
                    raise ValueError("snapshot exceeds traversal budget")
                result.append(entry.name)
        return sorted(result)

    def walk(directory: int, prefix: str = "") -> None:
        for name in names(directory):
            if is_residue_name(name):
                continue
            relative = prefix + name
            if len(relative) > 512:
                raise ValueError("snapshot path exceeds editable limit")
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ValueError("snapshot contains a link or special file")
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if stat.S_ISDIR(info.st_mode):
                flags |= os.O_DIRECTORY
            fd = os.open(name, flags, dir_fd=directory)
            try:
                opened = os.fstat(fd)
                if stat.S_ISDIR(opened.st_mode):
                    walk(fd, relative + "/")
                elif stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1:
                    if len(files) >= 100:
                        raise ValueError("snapshot exceeds 100 files")
                    with os.fdopen(os.dup(fd), "rb") as source:
                        files.append(edit_file(relative, source.read(MAX_FILE_BYTES + 1)))
                else:
                    raise ValueError("snapshot contains a link or special file")
            finally:
                os.close(fd)

    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            walk(fd)
        finally:
            os.close(fd)
    except (OSError, ValueError, RecursionError) as exc:
        raise SkillEditValidationError(
            "Cannot export a complete editing snapshot",
            [{"path": ".", "error": "unreadable, unsafe or oversized authoring tree"}],
        ) from exc
    return files
