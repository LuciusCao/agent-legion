import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def legacy_backup_path(db_path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return db_path.with_name(f"{db_path.stem}-before-v005-{timestamp}.sqlite")


def backup_sqlite_connection(conn: sqlite3.Connection, backup_path: Path) -> None:
    """Create a transactionally consistent SQLite backup, including WAL contents."""
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    destination = sqlite3.connect(backup_path)
    try:
        conn.backup(destination)
    finally:
        destination.close()
