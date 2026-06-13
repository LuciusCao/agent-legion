from __future__ import annotations

import contextlib
import sqlite3
from datetime import UTC, datetime


def _sqlite_timestamp(dt: datetime) -> str:
    """Return a UTC timestamp SQLite can compare reliably with current_timestamp."""
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def _rollback(conn: sqlite3.Connection) -> None:
    with contextlib.suppress(sqlite3.ProgrammingError):
        conn.execute("rollback")
