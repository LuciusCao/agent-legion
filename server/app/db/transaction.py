"""Unified connection/transaction contexts for SQLite access.

All write paths in ``server/app`` must go through :func:`write_transaction`
(the only place the literal ``begin immediate`` may appear) and read-only
paths through :func:`read_connection`. Combine with
``server.app.db.retry.retry_on_sqlite_lock`` for lock-contention retries —
the whole ``with write_transaction(...)`` block is the retry unit.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from server.app.db.connection import connect_sqlite


@contextmanager
def write_transaction(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a connection inside a ``begin immediate`` write transaction.

    Commits when the body returns normally; rolls back on exception. The
    connection is always closed. Do not nest write transactions.
    """
    conn = connect_sqlite(db_path)
    conn.isolation_level = None
    try:
        conn.execute("begin immediate")
        try:
            yield conn
        except Exception:
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute("rollback")
            raise
        else:
            conn.execute("commit")
    finally:
        conn.close()


@contextmanager
def read_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a read-only connection without an explicit transaction."""
    conn = connect_sqlite(db_path)
    try:
        yield conn
    finally:
        conn.close()
