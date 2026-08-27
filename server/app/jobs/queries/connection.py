"""Connection acquisition on the JobQueries facade (BOUNDARY-DATA-001, #187).

The facade's connection methods live in their own mixin so ``base.py`` stays
at its construction-only size budget: services replace
``read_connection(job_db.path)`` / ``write_transaction(job_db.path)`` with
``job_db.read()`` / ``job_db.write()`` and never need the DSN.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from server.app.db.connection import DatabaseConnection, connect_database
from server.app.db.transaction import read_connection, write_transaction

from .base import JobQueriesBase


class ConnectionQueriesMixin(JobQueriesBase):
    @contextmanager
    def connect(self) -> Iterator[DatabaseConnection]:
        conn = connect_database(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[DatabaseConnection]:
        """Pooled read connection (no transaction) — the facade replacement
        for ``read_connection(job_db.path)`` in service code."""
        with read_connection(self.path) as conn:
            yield conn

    @contextmanager
    def write(self) -> Iterator[DatabaseConnection]:
        """One committed PostgreSQL transaction — the facade replacement for
        ``write_transaction(job_db.path)`` in service code."""
        with write_transaction(self.path) as conn:
            yield conn

    @contextmanager
    def _connect_read(self) -> Iterator[DatabaseConnection]:
        with read_connection(self.path) as conn:
            yield conn
