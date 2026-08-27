from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from server.app.db.connection import DatabaseConnection, DatabaseDsn, connect_database
from server.app.db.schema import init_db
from server.app.db.transaction import read_connection, write_transaction


class JobQueriesBase:
    def __init__(self, path: DatabaseDsn, jobs_dir: Path):
        # `path`（DSN）保留为实例属性，仅供数据层自身（queries/atomic_
        # mutations）与 executors lease 仓储使用——BOUNDARY-DATA-001 的
        # service 检查按 job_db.path 计数，service 侧取连接一律走
        # connect/read/write 门面方法（#187：切断「任何拿到 JobQueries 的
        # service 都能自建连接」的 DSN 逃逸口）。
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
