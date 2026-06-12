import sqlite3

from server.app.db.migrations.runner import Migration


def _apply(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists workspace_executor_bootstrap_state (
          workspace_id text primary key,
          completed_at text not null default current_timestamp,
          foreign key(workspace_id) references workspaces(id) on delete cascade
        )
        """
    )


MIGRATION = Migration(version=2, name="executor_bootstrap_state", apply=_apply)
