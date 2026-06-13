import sqlite3

from server.app.db.migrations.errors import MigrationError
from server.app.db.migrations.models import Migration

_MIGRATION_VERSION = 5
_MIGRATION_NAME = "remove_legacy_executor_paths"


def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return column names in table declaration order."""
    return [row["name"] for row in conn.execute(f"pragma table_info({table})").fetchall()]


def _drop_pipeline_config_json(conn: sqlite3.Connection) -> None:
    """Remove pipeline_config_json without disabling FK checks mid-transaction."""
    cols = _column_names(conn, "workspaces")
    if "pipeline_config_json" not in cols:
        return
    try:
        conn.execute("alter table workspaces drop column pipeline_config_json")
    except sqlite3.DatabaseError as exc:
        raise MigrationError(
            "V005 requires SQLite 3.35+ ALTER TABLE DROP COLUMN support; "
            "the database was left unchanged"
        ) from exc


def _apply(conn: sqlite3.Connection) -> None:
    conn.execute("drop table if exists workspace_agent_assignments")
    conn.execute("drop table if exists workspace_executor_bootstrap_state")
    _drop_pipeline_config_json(conn)
    violations = conn.execute("pragma foreign_key_check").fetchall()
    if violations:
        details = "; ".join(str(row) for row in violations)
        raise RuntimeError(f"foreign key check failed for V005: {details}")


MIGRATION = Migration(
    version=_MIGRATION_VERSION,
    name=_MIGRATION_NAME,
    apply=_apply,
    rebuilds_fk=True,
)
