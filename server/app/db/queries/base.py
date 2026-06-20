from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from server.app.db.connection import connect_sqlite
from server.app.db.notifications import NotificationHub
from server.app.db.schema import init_db


class VideoQueriesBase:
    def __init__(
        self,
        path: Path,
        hub: NotificationHub | None = None,
        videos_dir: Path | None = None,
    ):
        self.path = path
        self._hub = hub
        self._videos_dir = videos_dir
        init_db(path)

    @contextmanager
    def connect(self):
        conn = connect_sqlite(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @contextmanager
    def _connect_read(self):
        """Read-only connection context that does not implicitly commit."""
        conn = connect_sqlite(self.path)
        try:
            yield conn
        finally:
            conn.close()

    def _row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None
