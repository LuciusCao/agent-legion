import sqlite3

from server.app.db.migrations.helpers import add_column_if_missing
from server.app.db.migrations.models import Migration


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (name,),
    ).fetchone()
    return row is not None


_WORKSPACE_PACKAGES_TABLE_SQL = """
create table if not exists workspace_packages (
  id integer primary key autoincrement,
  workspace_id text not null,
  path text not null,
  name text not null default '',
  job_count integer not null default 0,
  size_bytes integer not null default 0,
  locked integer not null default 0,
  created_at text not null default current_timestamp,
  foreign key(workspace_id) references workspaces(id) on delete cascade
)
"""

_INDEX_SQL = """
create index if not exists idx_workspace_packages_workspace_id
  on workspace_packages(workspace_id, created_at desc)
"""


def _apply(conn: sqlite3.Connection) -> None:
    conn.execute(_WORKSPACE_PACKAGES_TABLE_SQL)
    conn.execute(_INDEX_SQL)
    if _table_exists(conn, "jobs"):
        add_column_if_missing(
            conn,
            "jobs",
            "packed",
            "alter table jobs add column packed integer not null default 0",
        )


MIGRATION = Migration(version=12, name="workspace_packages", apply=_apply)
