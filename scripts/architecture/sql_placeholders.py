"""Permanent guard against SQLite-style ``?`` SQL placeholders.

The SQLite→PostgreSQL migration (issue #17) rewrote every placeholder to
psycopg's ``%s`` and retired the blind-rewrite shim in
``server/app/db/dialect.py`` (it would corrupt Postgres JSON operators
``?``, ``?|``, ``?&``). The baseline is empty; any ``?`` inside a SQL-looking
string literal under ``server/``, ``tests/``, or ``scripts/`` is an error.
``postgres_sql`` keeps a runtime guard for dynamically assembled SQL this
static check cannot see. The baseline loader lives in
``sql_placeholders_baseline.py``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from scripts.architecture.sql_placeholders_baseline import (
    _SqlPlaceholderConfigurationError,
    load_sql_placeholder_baseline,
)

__test__ = False

BASELINE_RELATIVE_PATH = "config/architecture/sql-placeholders-baseline.json"

_SQL_KEYWORD = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)

# Production and test code are both scanned: tests and scripts run against
# the same strict dialect guard, so a "?" there breaks at runtime too.
_SCAN_ROOTS = ("server", "tests", "scripts")

# Fixture files that intentionally contain "?" SQL strings.
_EXCLUDE = {
    "tests/test_architecture_sql_placeholders.py",  # this checker's own fixtures
    "tests/db/test_dialect_guard.py",  # runtime-guard fixtures keep one "?"
}


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
    """Count SQL ``?`` placeholders in server/, tests/, and scripts/ code."""
    counts: dict[str, int] = {}
    for scan_root in _SCAN_ROOTS:
        base = root / scan_root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            if relative in _EXCLUDE:
                continue
            count = count_sql_qmark_placeholders(path.read_text(encoding="utf-8"))
            if count:
                counts[relative] = count
    return counts


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
