"""Baseline loader for the SQL ``?`` placeholder guard.

Split out of ``sql_placeholders.py`` for the file-size budget; strict parsing
of ``config/architecture/sql-placeholders-baseline.json`` lives here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

__test__ = False


@dataclass(frozen=True)
class SqlPlaceholderBaseline:
    files: dict[str, int]


class _SqlPlaceholderConfigurationError(ValueError):
    """Internal configuration error captured by check_sql_placeholders."""

    pass


def load_sql_placeholder_baseline(path: Path) -> SqlPlaceholderBaseline:
    """Require exactly version 1 and a normalized positive count map."""
    if not path.is_file():
        raise _SqlPlaceholderConfigurationError(f"Baseline file not found: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _SqlPlaceholderConfigurationError(f"Malformed JSON in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise _SqlPlaceholderConfigurationError(
            f"Baseline root must be a mapping, got {type(raw).__name__}"
        )

    if set(raw) != {"version", "files"}:
        extra = set(raw) - {"version", "files"}
        missing = {"version", "files"} - set(raw)
        parts: list[str] = []
        if missing:
            parts.append(f"missing fields: {sorted(missing)}")
        if extra:
            parts.append(f"unknown fields: {sorted(extra)}")
        raise _SqlPlaceholderConfigurationError(f"Invalid baseline structure; {'; '.join(parts)}")

    version = raw.get("version")
    if type(version) is not int or version != 1:
        raise _SqlPlaceholderConfigurationError(f"Unsupported baseline version: {version!r}")

    files = raw.get("files")
    if not isinstance(files, dict):
        raise _SqlPlaceholderConfigurationError("files must be a mapping")

    normalized: dict[str, int] = {}
    for key, value in files.items():
        if not isinstance(key, str):
            raise _SqlPlaceholderConfigurationError("baseline path keys must be strings")
        if type(value) is not int:
            raise _SqlPlaceholderConfigurationError(f"baseline count for {key} must be an integer")
        if value <= 0:
            raise _SqlPlaceholderConfigurationError(f"baseline count for {key} must be positive")
        normalized_key = str(PurePosixPath(key))
        if normalized_key in normalized:
            raise _SqlPlaceholderConfigurationError(
                f"duplicate normalized baseline path: {normalized_key}"
            )
        normalized[normalized_key] = value

    return SqlPlaceholderBaseline(files=normalized)
