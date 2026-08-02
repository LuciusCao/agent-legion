"""Ratchet guard against new SQLite-style ``?`` SQL placeholders.

``server/app/db/dialect.py`` blindly rewrites every ``?`` to psycopg's ``%s``,
which would corrupt Postgres JSON operators (``?``, ``?|``, ``?&``). New SQL
must be written with ``%s`` directly; existing ``?`` usage is recorded in
``config/architecture/sql-placeholders-baseline.json`` and may only shrink.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

__test__ = False

BASELINE_RELATIVE_PATH = "config/architecture/sql-placeholders-baseline.json"

_SQL_KEYWORD = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)


@dataclass(frozen=True)
class SqlPlaceholderBaseline:
    files: dict[str, int]


class _SqlPlaceholderConfigurationError(ValueError):
    """Internal configuration error captured by check_sql_placeholders."""

    pass


def count_sql_qmark_placeholders(source: str) -> int:
    """Count ``?`` inside string literals that look like SQL statements."""
    tree = ast.parse(source)
    total = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if "?" in value and _SQL_KEYWORD.search(value):
                total += value.count("?")
    return total


def collect_sql_placeholder_counts(root: Path) -> dict[str, int]:
    """Count SQL ``?`` placeholders for every server/**/*.py file."""
    counts: dict[str, int] = {}
    server_root = root / "server"
    if not server_root.is_dir():
        return counts
    for path in sorted(server_root.rglob("*.py")):
        count = count_sql_qmark_placeholders(path.read_text(encoding="utf-8"))
        if count:
            counts[path.relative_to(root).as_posix()] = count
    return counts


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


def check_sql_placeholders(root: Path) -> list[str]:
    """Reject SQL ``?`` placeholders above the baseline or in new files."""
    try:
        baseline = load_sql_placeholder_baseline(root / BASELINE_RELATIVE_PATH)
    except _SqlPlaceholderConfigurationError as exc:
        return [f"sql placeholder configuration: {exc}"]

    errors: list[str] = []
    for path, count in collect_sql_placeholder_counts(root).items():
        allowed = baseline.files.get(path)
        if allowed is None:
            errors.append(
                f"{path}: {count} SQL '?' placeholder(s) in a file without baseline entry; "
                "write new SQL with psycopg '%s' placeholders instead"
            )
        elif count > allowed:
            errors.append(
                f"{path}: {count} SQL '?' placeholders exceeds baseline {allowed}; "
                "write new SQL with psycopg '%s' placeholders instead"
            )
    return sorted(errors)
