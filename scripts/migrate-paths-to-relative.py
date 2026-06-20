#!/usr/bin/env python3
# ruff: noqa: E402
"""Migrate historical absolute storage paths to relative canonical values."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Make the project root importable when the script is invoked directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.app.db.connection import connect_sqlite
from server.app.executors.backup import backup_sqlite_connection
from server.app.settings import load_settings


@dataclass(frozen=True)
class PathColumnSpec:
    table: str
    pk_column: str
    path_column: str
    expected_category: str


AFFECTED_COLUMNS: list[PathColumnSpec] = [
    PathColumnSpec("videos", "id", "storage_dir", "videos"),
    PathColumnSpec("phase_runs", "id", "log_path", "logs"),
    PathColumnSpec("jobs", "id", "storage_dir", "jobs"),
    PathColumnSpec("node_runs", "id", "log_path", "logs"),
    PathColumnSpec("node_runs", "id", "run_dir", "jobs"),
    PathColumnSpec("node_runs", "id", "session_dir", "jobs"),
    PathColumnSpec("packages", "id", "path", "packages"),
]


class PathMigrationError(ValueError):
    """Raised when a stored path cannot be migrated safely."""

    def __init__(
        self,
        message: str,
        *,
        table: str = "",
        column: str = "",
        row_id: str = "",
    ) -> None:
        super().__init__(message)
        self.table = table
        self.column = column
        self.row_id = row_id


def _validate_relative_value(value: str, spec: PathColumnSpec, row_id: str) -> None:
    """Validate an already-relative canonical value.

    Rejects ``..`` components and values whose first part does not match the
    expected managed category for the column.
    """
    parts = Path(value).parts
    if not parts:
        raise PathMigrationError(
            "relative value is empty",
            table=spec.table,
            column=spec.path_column,
            row_id=row_id,
        )
    if any(part == ".." for part in parts):
        raise PathMigrationError(
            "relative value escapes root using '..'",
            table=spec.table,
            column=spec.path_column,
            row_id=row_id,
        )
    if parts[0] != spec.expected_category:
        raise PathMigrationError(
            f"relative value starts with '{parts[0]}', expected '{spec.expected_category}'",
            table=spec.table,
            column=spec.path_column,
            row_id=row_id,
        )


def _convert_absolute_value(
    value: str,
    old_data_dir: Path,
    spec: PathColumnSpec,
    row_id: str,
) -> str:
    """Convert an absolute path to a canonical POSIX value relative to ``old_data_dir``.

    The resolved path must be strictly inside ``old_data_dir`` and its relative
    suffix must begin with the expected category for the column.
    """
    candidate = Path(value)
    try:
        resolved = candidate.resolve()
        resolved_old = old_data_dir.resolve()
        relative = resolved.relative_to(resolved_old)
    except (ValueError, RuntimeError) as exc:
        raise PathMigrationError(
            "absolute path is not inside old_data_dir",
            table=spec.table,
            column=spec.path_column,
            row_id=row_id,
        ) from exc

    if not relative.parts or relative.parts[0] != spec.expected_category:
        actual = relative.parts[0] if relative.parts else ""
        raise PathMigrationError(
            f"relative suffix starts with '{actual}', expected '{spec.expected_category}'",
            table=spec.table,
            column=spec.path_column,
            row_id=row_id,
        )
    if any(part == ".." for part in relative.parts):
        raise PathMigrationError(
            "relative suffix escapes root using '..'",
            table=spec.table,
            column=spec.path_column,
            row_id=row_id,
        )
    return relative.as_posix()


def _process_value(
    value: str,
    old_data_dir: Path,
    spec: PathColumnSpec,
    row_id: str,
) -> str | None:
    """Return the migrated value, or ``None`` when the row does not need updating."""
    if value == "":
        return None

    candidate = Path(value)
    if candidate.is_absolute():
        return _convert_absolute_value(value, old_data_dir, spec, row_id)

    _validate_relative_value(value, spec, row_id)
    return None


def _migrate_column(
    conn: sqlite3.Connection,
    old_data_dir: Path,
    spec: PathColumnSpec,
) -> int:
    """Validate and update all rows for a single affected column.

    Returns the number of rows that need updating. Raises ``PathMigrationError``
    on the first invalid value so that the caller can roll back the transaction.
    """
    select_sql = f"select {spec.pk_column}, {spec.path_column} from {spec.table}"
    updates: list[tuple[str, Any]] = []

    for row in conn.execute(select_sql):
        row_id = row[spec.pk_column]
        value: str = row[spec.path_column]
        new_value = _process_value(value, old_data_dir, spec, str(row_id))
        if new_value is not None:
            updates.append((new_value, row_id))

    update_sql = f"update {spec.table} set {spec.path_column} = ? where {spec.pk_column} = ?"
    for new_value, row_id in updates:
        conn.execute(update_sql, (new_value, row_id))

    return len(updates)


def _timestamped_backup_path(db_path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return db_path.with_name(f"{db_path.stem}-before-relative-path-migration-{timestamp}.sqlite")


def run_migration(
    db_path: Path,
    old_data_dir: Path,
    *,
    dry_run: bool = False,
    on_backup: Callable[[Path], None] | None = None,
) -> dict[tuple[str, str], int]:
    """Validate and migrate all affected columns.

    ``dry_run`` performs the same validation and reports counts, then rolls back
    without creating a backup. A real run checkpoints SQLite, creates a
    timestamped backup beside the database file, updates all columns in one
    transaction, and reports counts.
    """
    conn = connect_sqlite(db_path)
    conn.isolation_level = None
    try:
        if not dry_run:
            conn.execute("pragma wal_checkpoint(TRUNCATE)")
        conn.execute("BEGIN IMMEDIATE")
        try:
            if not dry_run:
                backup_path = _timestamped_backup_path(db_path)
                backup_source = connect_sqlite(db_path)
                try:
                    backup_sqlite_connection(backup_source, backup_path)
                finally:
                    backup_source.close()
                if on_backup is not None:
                    on_backup(backup_path)

            counts: dict[tuple[str, str], int] = {}
            for spec in AFFECTED_COLUMNS:
                counts[(spec.table, spec.path_column)] = _migrate_column(conn, old_data_dir, spec)
        except Exception:
            conn.rollback()
            raise

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()

    return counts


def _print_counts(counts: dict[tuple[str, str], int]) -> None:
    for (table, column), count in counts.items():
        print(f"{table}.{column}: {count}")
    print(f"total updated rows: {sum(counts.values())}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate historical absolute storage paths to relative canonical values."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report counts without writing changes.",
    )
    parser.add_argument(
        "--old-data-dir",
        type=Path,
        default=None,
        help="Historical data directory root (defaults to the active data_dir).",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    old_data_dir = args.old_data_dir or settings.data_dir
    db_path = settings.data_dir / "video_hive.sqlite"

    try:
        counts = run_migration(
            db_path,
            old_data_dir,
            dry_run=args.dry_run,
            on_backup=lambda path: print(f"Backup: {path}"),
        )
    except PathMigrationError as exc:
        print(
            f"{exc.table}.{exc.column} row {exc.row_id}: {exc}",
            file=sys.stderr,
        )
        return 1

    _print_counts(counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
