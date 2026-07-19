import sqlite3

from server.app.db.migrations.helpers import add_column_if_missing
from server.app.db.migrations.models import Migration


def _apply(conn: sqlite3.Connection) -> None:
    # Nullable: payloads submitted without a command spec (legacy rows) simply
    # have nothing to pass through to claims; the broker never renders specs.
    ddl = "alter table remote_executions add column command_spec_json text"
    add_column_if_missing(conn, "remote_executions", "command_spec_json", ddl)


MIGRATION = Migration(version=23, name="remote_execution_command_spec", apply=_apply)
