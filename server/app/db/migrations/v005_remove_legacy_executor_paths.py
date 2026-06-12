import sqlite3

from server.app.db.migrations.models import Migration

_MIGRATION_VERSION = 5
_MIGRATION_NAME = "remove_legacy_executor_paths"

# Schema for the table-copy fallback when ALTER TABLE DROP COLUMN is unavailable.
_WORKSPACES_TABLE_SQL = """
create table workspaces__v005 (
  id text primary key,
  name text not null,
  description text not null default '',
  default_pipeline_key text not null default 'question_content',
  cms_config_json text not null default '{}',
  resource_config_json text not null default '{}',
  created_at text not null default current_timestamp,
  updated_at text not null default current_timestamp,
  default_entity text not null default 'question',
  intake_config_json text not null default '{}'
)
"""


def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return column names in table declaration order."""
    return [row["name"] for row in conn.execute(f"pragma table_info({table})").fetchall()]


def _drop_pipeline_config_json(conn: sqlite3.Connection) -> None:
    """Remove pipeline_config_json from workspaces, preferring DROP COLUMN."""
    try:
        conn.execute("alter table workspaces drop column pipeline_config_json")
        return
    except sqlite3.OperationalError:
        pass

    # Fallback: table copy with foreign keys temporarily disabled because
    # workspaces is referenced by many tables.
    conn.execute("pragma foreign_keys=OFF")
    try:
        conn.execute("alter table workspaces rename to workspaces__v005_old")
        conn.execute(_WORKSPACES_TABLE_SQL.replace("workspaces__v005", "workspaces__v005_new"))

        old_cols = _column_names(conn, "workspaces__v005_old")
        new_cols = _column_names(conn, "workspaces__v005_new")
        keep_cols = [col for col in old_cols if col in new_cols]
        col_list = ", ".join(keep_cols)
        conn.execute(
            f"insert into workspaces__v005_new ({col_list}) select {col_list} from workspaces__v005_old"
        )

        old_count = conn.execute("select count(*) from workspaces__v005_old").fetchone()[0]
        new_count = conn.execute("select count(*) from workspaces__v005_new").fetchone()[0]
        if old_count != new_count:
            raise RuntimeError(
                f"workspaces row count mismatch after V005 copy: {new_count} vs {old_count}"
            )

        conn.execute("drop table workspaces__v005_old")
        conn.execute("alter table workspaces__v005_new rename to workspaces")
        conn.execute(
            "create index if not exists idx_workspaces_created_at on workspaces(created_at)"
        )
    finally:
        conn.execute("pragma foreign_keys=ON")


def _apply(conn: sqlite3.Connection) -> None:
    conn.execute("drop table if exists workspace_agent_assignments")
    conn.execute("drop table if exists workspace_executor_bootstrap_state")
    _drop_pipeline_config_json(conn)
    # _drop_pipeline_config_json restores pragma foreign_keys=ON in its finally block,
    # so the explicit FK check runs with enforcement enabled.
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
