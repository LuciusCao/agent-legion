import sqlite3

from server.app.db.migrations.runner import Migration

_OLD_COLUMNS = [
    ("workspaces", "default_pipeline_key", "default_workflow_key"),
    ("job_batches", "pipeline_key", "workflow_key"),
    ("jobs", "pipeline_key", "workflow_key"),
    ("workspace_node_bindings", "pipeline_key", "workflow_key"),
    ("workspace_node_limits", "pipeline_key", "workflow_key"),
    ("executor_leases", "pipeline_key", "workflow_key"),
]

_OLD_INDEXES = [
    (
        "idx_jobs_pipeline_status",
        "idx_jobs_workflow_status",
        "create index if not exists idx_jobs_workflow_status on jobs(workflow_key, status)",
    ),
    (
        "idx_jobs_source",
        "idx_jobs_workflow_source",
        "create index if not exists idx_jobs_workflow_source on jobs(workflow_key, source_type, source_id)",
    ),
    (
        "idx_jobs_workspace_pipeline_status",
        "idx_jobs_workspace_workflow_status",
        "create index if not exists idx_jobs_workspace_workflow_status on jobs(workspace_id, workflow_key, status)",
    ),
    (
        "idx_jobs_workspace_source",
        "idx_jobs_workspace_workflow_source",
        "create index if not exists idx_jobs_workspace_workflow_source on jobs(workspace_id, workflow_key, source_type, source_id)",
    ),
    (
        "idx_executor_leases_node_active",
        "idx_executor_leases_workflow_node_active",
        "create index if not exists idx_executor_leases_workflow_node_active on executor_leases(workspace_id, workflow_key, node_key, status, expires_at)",
    ),
]


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"pragma table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def _index_exists(conn: sqlite3.Connection, index: str) -> bool:
    row = conn.execute(
        "select 1 from sqlite_master where type='index' and name=?",
        (index,),
    ).fetchone()
    return row is not None


def _needs_backup(conn: sqlite3.Connection) -> bool:
    return any(_column_exists(conn, table, old_col) for table, old_col, _ in _OLD_COLUMNS)


def _apply(conn: sqlite3.Connection) -> None:
    for table, old_col, new_col in _OLD_COLUMNS:
        if _column_exists(conn, table, old_col):
            conn.execute(f"alter table {table} rename column {old_col} to {new_col}")

    for old_idx, new_idx, new_idx_sql in _OLD_INDEXES:
        if _index_exists(conn, old_idx):
            conn.execute(f"drop index if exists {old_idx}")
        if not _index_exists(conn, new_idx):
            conn.execute(new_idx_sql)


MIGRATION = Migration(
    version=7,
    name="rename_pipeline_to_workflow",
    apply=_apply,
    backup_label="v007",
    backup_when=_needs_backup,
)
