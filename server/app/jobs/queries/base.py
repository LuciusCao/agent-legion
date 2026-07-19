from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from server.app.db.connection import connect_sqlite
from server.app.db.schema import init_db
from server.app.db.transaction import read_connection


class JobQueriesBase:
    def __init__(self, path: Path, jobs_dir: Path):
        self.path = path
        self.jobs_dir = jobs_dir
        init_db(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = connect_sqlite(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @contextmanager
    def _connect_read(self) -> Iterator[sqlite3.Connection]:
        with read_connection(self.path) as conn:
            yield conn
