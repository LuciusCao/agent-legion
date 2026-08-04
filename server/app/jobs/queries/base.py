from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from server.app.db.connection import DatabaseConnection, DatabaseDsn, connect_database
from server.app.db.schema import init_db
from server.app.db.transaction import read_connection


class JobQueriesBase:
    def __init__(self, path: DatabaseDsn, jobs_dir: Path):
        self.path = path
        self.jobs_dir = jobs_dir
        init_db(path)

    @contextmanager
    def connect(self) -> Iterator[DatabaseConnection]:
        conn = connect_database(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @contextmanager
    def _connect_read(self) -> Iterator[DatabaseConnection]:
        with read_connection(self.path) as conn:
            yield conn
