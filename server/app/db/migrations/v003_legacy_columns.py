import sqlite3

from server.app.db.migrations.helpers import add_column_if_missing
from server.app.db.migrations.models import Migration

# Hard-coded table identifiers used by this migration. These are never
# constructed from user input.
_TABLE_VIDEOS = "videos"
_TABLE_WORKSPACES = "workspaces"
_TABLE_PACKAGES = "packages"
_TABLE_JOB_BATCHES = "job_batches"
_TABLE_JOBS = "jobs"
_TABLE_NODE_RUNS = "node_runs"

# Each tuple is (column_name, column_type_and_constraints). Identifiers are
# constants; DDL strings are assembled locally with f-strings.
_VIDEO_COLUMNS: tuple[tuple[str, str], ...] = (
    ("content_type", "text not null default 'knowledge'"),
    ("external_id", "text not null default ''"),
    ("knowledge_code", "text not null default ''"),
    ("question_id", "text not null default ''"),
    ("source_uuid", "text not null default ''"),
    ("packed", "integer not null default 0"),
    ("interaction_stats_json", "text not null default ''"),
    ("interaction_review_status", "text not null default ''"),
)

_WORKSPACE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("cms_config_json", "text not null default '{}'"),
    ("resource_config_json", "text not null default '{}'"),
    ("default_entity", "text not null default 'question'"),
    ("intake_config_json", "text not null default '{}'"),
    ("description", "text not null default ''"),
    ("pipeline_config_json", "text not null default '{}'"),
)

_PACKAGE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("video_count", "integer not null default 0"),
    ("size_bytes", "integer not null default 0"),
    ("name", "text not null default ''"),
    ("locked", "integer not null default 0"),
)

_JOB_BATCH_COLUMNS: tuple[tuple[str, str], ...] = (
    ("workspace_id", "text not null default 'default'"),
)

_JOB_COLUMNS: tuple[tuple[str, str], ...] = (
    ("workspace_id", "text not null default 'default'"),
    ("stem", "text not null default ''"),
)

_NODE_RUN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_dir", "text not null default ''"),
    ("session_dir", "text not null default ''"),
)


def _add_columns(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[tuple[str, str], ...],
) -> None:
    for column, ddl_fragment in columns:
        add_column_if_missing(
            conn,
            table,
            column,
            f"alter table {table} add column {column} {ddl_fragment}",
        )


def _apply(conn: sqlite3.Connection) -> None:
    _add_columns(conn, _TABLE_VIDEOS, _VIDEO_COLUMNS)
    _add_columns(conn, _TABLE_WORKSPACES, _WORKSPACE_COLUMNS)
    _add_columns(conn, _TABLE_PACKAGES, _PACKAGE_COLUMNS)
    _add_columns(conn, _TABLE_JOB_BATCHES, _JOB_BATCH_COLUMNS)
    _add_columns(conn, _TABLE_JOBS, _JOB_COLUMNS)
    _add_columns(conn, _TABLE_NODE_RUNS, _NODE_RUN_COLUMNS)


MIGRATION = Migration(version=3, name="legacy_columns", apply=_apply)
