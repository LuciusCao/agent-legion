"""Atomic rollback-state and managed-profile primitives for the native probe."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_evidence_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def load_rollback_baseline(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"rollback baseline unreadable: {type(exc).__name__}: {exc}"
    if (
        not isinstance(previous, dict)
        or not isinstance(previous.get("before"), dict)
        or not isinstance(previous.get("before_profiles"), dict)
    ):
        return None, "rollback baseline is missing trusted before state"
    return previous, None


def managed_profile_valid(path: Path, expected_sha256: str) -> bool:
    managed_stat = path.stat()
    return (
        not path.is_symlink()
        and managed_stat.st_uid == 0
        and managed_stat.st_gid == 0
        and stat.S_IMODE(managed_stat.st_mode) == 0o644
        and _sha256(path) == expected_sha256
    )


def read_profiles(path: Path) -> dict[str, str]:
    rows = {}
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 2:
            rows[fields[0]] = fields[1]
    return rows
