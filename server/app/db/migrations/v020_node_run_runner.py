from __future__ import annotations

import sqlite3

from server.app.db.migrations.models import Migration


def _apply(conn: sqlite3.Connection) -> None:
    conn.execute("alter table node_runs add column runner text not null default ''")


MIGRATION = Migration(version=20, name="node_run_runner", apply=_apply)
