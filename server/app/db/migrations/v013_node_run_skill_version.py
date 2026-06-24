import sqlite3

from server.app.db.migrations.helpers import add_column_if_missing
from server.app.db.migrations.models import Migration


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _apply(conn: sqlite3.Connection) -> None:
    # Incomplete legacy test schemas may not have created node_runs yet.
    if not _table_exists(conn, "node_runs"):
        return
    add_column_if_missing(
        conn,
        "node_runs",
        "skill_version",
        "alter table node_runs add column skill_version text not null default ''",
    )


MIGRATION = Migration(version=13, name="node_run_skill_version", apply=_apply)
