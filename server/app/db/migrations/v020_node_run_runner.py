import sqlite3

from server.app.db.migrations.helpers import add_column_if_missing
from server.app.db.migrations.models import Migration


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    sql = "select 1 from sqlite_master where type = 'table' and name = ?"
    return conn.execute(sql, (name,)).fetchone() is not None


def _apply(conn: sqlite3.Connection) -> None:
    # Incomplete legacy test schemas may not have created node_runs yet.
    if not _table_exists(conn, "node_runs"):
        return
    ddl = "alter table node_runs add column runner text not null default ''"
    add_column_if_missing(conn, "node_runs", "runner", ddl)


MIGRATION = Migration(version=20, name="node_run_runner", apply=_apply)
