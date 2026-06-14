import os
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class RestoreError(RuntimeError):
    """Raised when a SQLite backup cannot be restored safely."""


def legacy_backup_path(db_path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return db_path.with_name(f"{db_path.stem}-before-v005-{timestamp}.sqlite")


def _restore_backup_path(db_path: Path, label: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return db_path.with_name(f"{db_path.stem}-{label}-{timestamp}.sqlite")


def _has_active_wal(db_path: Path) -> bool:
    """Return True when an associated WAL file contains uncheckpointed data."""
    wal_path = db_path.with_name(f"{db_path.name}-wal")
    return wal_path.exists() and wal_path.stat().st_size > 0


def quiesce_sqlite_database(db_path: Path) -> None:
    """Checkpoint and truncate the WAL so the database file is self-contained.

    This is exposed primarily for tests and restore preparation.  It raises
    :class:`RestoreError` when the database is busy or otherwise inaccessible.
    """
    try:
        conn = sqlite3.connect(str(db_path), timeout=1.0)
    except sqlite3.Error as exc:
        raise RestoreError(f"cannot open database to quiesce: {exc}") from exc
    try:
        conn.execute("pragma wal_checkpoint(TRUNCATE)")
    except sqlite3.OperationalError as exc:
        raise RestoreError(f"database is active or busy: {exc}") from exc
    finally:
        conn.close()


def backup_sqlite_connection(conn: sqlite3.Connection, backup_path: Path) -> None:
    """Create a transactionally consistent SQLite backup, including WAL contents."""
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    destination = sqlite3.connect(backup_path)
    try:
        conn.backup(destination)
    finally:
        destination.close()


def _validate_sqlite_database(path: Path) -> list[dict[str, Any]]:
    try:
        conn = sqlite3.connect(str(path), timeout=5.0)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("pragma foreign_keys=ON")
            integrity = conn.execute("pragma integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RestoreError(f"integrity check failed: {integrity}")
            if conn.execute("pragma foreign_key_check").fetchall():
                raise RestoreError("foreign key check failed")
            return [
                dict(row)
                for row in conn.execute(
                    "select version, name from schema_migrations order by version"
                ).fetchall()
            ]
        finally:
            conn.close()
    except (sqlite3.Error, RestoreError) as exc:
        if isinstance(exc, RestoreError):
            raise
        raise RestoreError(f"backup validation failed: {exc}") from exc


def restore_sqlite_database(
    backup_path: Path,
    db_path: Path,
) -> tuple[Path, list[dict[str, Any]]]:
    """Replace ``db_path`` with ``backup_path`` atomically in the same directory.

    The current database is copied to a timestamped preserved path before the
    replacement.  The operation refuses to run when the target has an active
    WAL file, ensuring no open connection can lose uncommitted writes.

    Returns the path to the preserved previous database and the migration
    history rows from the restored database.
    """
    if not backup_path.is_file():
        raise RestoreError(f"backup not found: {backup_path}")
    if not db_path.is_file():
        raise RestoreError(f"target database not found: {db_path}")
    if _has_active_wal(db_path):
        raise RestoreError("database has an active WAL; close all connections and checkpoint first")

    preserved = _restore_backup_path(db_path, "before-restore")
    staging = _restore_backup_path(db_path, "restore-staging")

    try:
        # Validate the exact staged bytes before they can replace the live DB.
        shutil.copy2(backup_path, staging)
        history = _validate_sqlite_database(staging)

        # Preserve the current database and any associated WAL/SHM files.
        shutil.copy2(db_path, preserved)
        for suffix in ("-wal", "-shm"):
            sibling = db_path.with_name(f"{db_path.name}{suffix}")
            if sibling.exists():
                shutil.copy2(sibling, preserved.with_name(f"{preserved.name}{suffix}"))

        os.replace(staging, db_path)

        # Remove stale WAL/SHM siblings left over from the previous database.
        for suffix in ("-wal", "-shm"):
            stale = db_path.with_name(f"{db_path.name}{suffix}")
            if stale.exists():
                stale.unlink()
    except OSError as exc:
        raise RestoreError(f"atomic replace failed: {exc}") from exc
    finally:
        if staging.exists():
            staging.unlink()

    try:
        history = _validate_sqlite_database(db_path)
    except RestoreError as exc:
        rollback = _restore_backup_path(db_path, "restore-rollback")
        try:
            shutil.copy2(preserved, rollback)
            os.replace(rollback, db_path)
        except OSError as rollback_exc:
            raise RestoreError(
                f"restored database validation failed and rollback failed: {rollback_exc}"
            ) from exc
        finally:
            if rollback.exists():
                rollback.unlink()
        raise RestoreError(f"restored database validation failed: {exc}") from exc

    return preserved, history
