from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import cast

from server.app.db.connection import DatabaseDsn, connect_database
from server.app.db.notifications import NotificationHub
from server.app.db.schema import init_db
from server.app.db.transaction import read_connection
from server.app.records import VideoRecord


class VideoQueriesBase:
    def __init__(
        self,
        path: DatabaseDsn,
        hub: NotificationHub | None = None,
        videos_dir: Path | None = None,
    ):
        self.path = path
        self._hub = hub
        self._videos_dir = videos_dir
        init_db(path)

    @contextmanager
    def connect(self):
        conn = connect_database(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @contextmanager
    def _connect_read(self):
        """Read-only connection context that does not implicitly commit."""
        with read_connection(self.path) as conn:
            yield conn

    def _row(self, row: dict | None) -> VideoRecord | None:
        return cast(VideoRecord, dict(row)) if row else None
