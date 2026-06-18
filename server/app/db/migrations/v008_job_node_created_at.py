import sqlite3

from server.app.db.migrations.helpers import add_column_if_missing
from server.app.db.migrations.models import Migration

_TABLE = "job_nodes"


def _apply(conn: sqlite3.Connection) -> None:
    # SQLite ALTER TABLE does not allow a non-constant default such as
    # current_timestamp, so add the column without a default and backfill.
    add_column_if_missing(
        conn,
        _TABLE,
        "created_at",
        f"alter table {_TABLE} add column created_at text",
    )
    # Backfill existing rows so that historical wait times are not distorted:
    # use the node's start time when available, otherwise treat it as queued now.
    conn.execute(
        f"""
        update {_TABLE}
        set created_at = coalesce(started_at, current_timestamp)
        where created_at is null
        """
    )


MIGRATION = Migration(version=8, name="job_node_created_at", apply=_apply)
