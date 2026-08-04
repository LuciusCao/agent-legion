from __future__ import annotations

from datetime import UTC, datetime

from server.app.db.connection import DatabaseConnection


def database_timestamp(dt: datetime) -> str:
    """Normalize timestamps at the application's stable string boundary."""
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def _rollback(conn: DatabaseConnection) -> None:
    conn.rollback()
