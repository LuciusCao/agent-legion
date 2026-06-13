import sqlite3

from server.app.db.migrations.helpers import add_column_if_missing
from server.app.db.migrations.models import Migration

_TABLE_JOBS = "jobs"

_JOB_COLUMNS: tuple[tuple[str, str], ...] = (
    (
        "execution_mode",
        "text not null default 'full' check(execution_mode in ('full', 'until_node'))",
    ),
    ("target_node_key", "text"),
    ("execution_paused", "integer not null default 0 check(execution_paused in (0, 1))"),
    ("pause_reason", "text not null default ''"),
)


def _apply(conn: sqlite3.Connection) -> None:
    for column, ddl_fragment in _JOB_COLUMNS:
        add_column_if_missing(
            conn,
            _TABLE_JOBS,
            column,
            f"alter table {_TABLE_JOBS} add column {column} {ddl_fragment}",
        )


MIGRATION = Migration(version=6, name="job_execution_control", apply=_apply)
