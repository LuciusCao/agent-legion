"""Unified PostgreSQL connection and transaction contexts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from server.app.db.connection import DatabaseConnection, DatabaseDsn, connect_database


@contextmanager
def write_transaction(database_dsn: DatabaseDsn) -> Iterator[DatabaseConnection]:
    """Yield one PostgreSQL transaction and deterministically release it."""
    conn = connect_database(database_dsn)
    try:
        conn.execute("begin")
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()
    finally:
        conn.close()


@contextmanager
def read_connection(database_dsn: DatabaseDsn) -> Iterator[DatabaseConnection]:
    """Yield a pooled PostgreSQL connection for bounded read operations."""
    conn = connect_database(database_dsn)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()
