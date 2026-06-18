import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from server.app.db.migrations.models import Migration

logger = logging.getLogger(__name__)


def _backup_before_migration(conn: sqlite3.Connection, label: str) -> Path | None:
    row = conn.execute("pragma database_list").fetchone()
    if row is None or not row["file"]:
        return None
    database_path = Path(str(row["file"]))
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    backup_path = database_path.with_name(f"{database_path.stem}-before-{label}-{timestamp}.sqlite")
    destination = sqlite3.connect(backup_path)
    try:
        conn.backup(destination)
    finally:
        destination.close()
    return backup_path


def backup_if_requested(conn: sqlite3.Connection, migration: Migration) -> None:
    if not migration.backup_label:
        return
    if migration.backup_when is not None and not migration.backup_when(conn):
        return
    backup_path = _backup_before_migration(conn, migration.backup_label)
    if backup_path is not None:
        logger.info("Created pre-migration backup for %s at %s", migration.name, backup_path)
