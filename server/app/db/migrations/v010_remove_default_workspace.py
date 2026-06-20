import sqlite3

from server.app.db.migrations.models import Migration


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _apply(conn: sqlite3.Connection) -> None:
    # Removing the default workspace cascades to all jobs, batches, nodes,
    # runs, and leases that referenced it. The application no longer seeds or
    # falls back to a "default" workspace; all jobs must belong to an explicit
    # workspace created through the API.
    if not _table_exists(conn, "workspaces"):
        return
    conn.execute("delete from workspaces where id = 'default'")


MIGRATION = Migration(version=10, name="remove_default_workspace", apply=_apply)
