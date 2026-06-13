import sqlite3

from server.app.db.migrations.helpers import add_column_if_missing
from server.app.db.migrations.hooks import _call_phase_hook
from server.app.db.migrations.models import Migration
from server.app.db.migrations.report import MigrationIssue, MigrationReport, raise_blocked

_JOB_BATCHES_TABLE_SQL = """
create table job_batches__v004 (
  id text primary key,
  workspace_id text not null default 'default',
  pipeline_key text not null,
  source_kind text not null,
  source_payload_json text not null default '{}',
  status text not null default 'created',
  created_count integer not null default 0,
  error_message text not null default '',
  created_at text not null default current_timestamp,
  foreign key(workspace_id) references workspaces(id) on delete cascade
)
"""

_JOBS_TABLE_SQL = """
create table jobs__v004 (
  id text primary key,
  workspace_id text not null default 'default',
  pipeline_key text not null,
  source_type text not null,
  source_id text not null,
  batch_id text not null default '',
  title text not null default '',
  status text not null default 'queued',
  storage_dir text not null default '',
  error_message text not null default '',
  stem text not null default '',
  created_at text not null default current_timestamp,
  updated_at text not null default current_timestamp,
  execution_mode text not null default 'full' check(execution_mode in ('full', 'until_node')),
  target_node_key text,
  execution_paused integer not null default 0 check(execution_paused in (0, 1)),
  pause_reason text not null default '',
  foreign key(workspace_id) references workspaces(id) on delete cascade
)
"""

_JOB_NODES_TABLE_SQL = """
create table job_nodes__v004 (
  id integer primary key autoincrement,
  job_id text not null,
  node_key text not null,
  status text not null default 'pending',
  stale_reason text not null default '',
  error_message text not null default '',
  started_at text,
  finished_at text,
  unique(job_id, node_key),
  foreign key(job_id) references jobs(id) on delete cascade
)
"""

_NODE_RUNS_TABLE_SQL = """
create table node_runs__v004 (
  id integer primary key autoincrement,
  job_id text not null,
  node_key text not null,
  status text not null,
  started_at text not null default current_timestamp,
  finished_at text,
  command_json text not null default '[]',
  exit_code integer,
  log_path text not null default '',
  error_message text not null default '',
  run_dir text not null default '',
  session_dir text not null default '',
  foreign key(job_id) references jobs(id) on delete cascade
)
"""

_EXECUTOR_LEASES_TABLE_SQL = """
create table executor_leases__v004 (
  id text primary key,
  execution_id text not null unique,
  executor_id text not null,
  workspace_id text not null,
  job_id text not null,
  pipeline_key text not null,
  node_key text not null,
  node_run_id integer not null,
  status text not null check(status in ('active', 'released', 'expired')),
  acquired_at text not null,
  heartbeat_at text not null,
  expires_at text not null,
  foreign key(workspace_id) references workspaces(id) on delete cascade,
  foreign key(job_id) references jobs(id) on delete cascade,
  foreign key(node_run_id) references node_runs(id) on delete cascade
)
"""

_TABLES: tuple[tuple[str, str, str, tuple[str, ...], bool], ...] = (
    (
        "job_batches",
        _JOB_BATCHES_TABLE_SQL,
        "job_batches__v004",
        ("create index idx_job_batches_workspace on job_batches(workspace_id, created_at)",),
        True,
    ),
    (
        "jobs",
        _JOBS_TABLE_SQL,
        "jobs__v004",
        (
            "create index idx_jobs_pipeline_status on jobs(pipeline_key, status)",
            "create index idx_jobs_source on jobs(pipeline_key, source_type, source_id)",
            "create index idx_jobs_workspace_pipeline_status on jobs(workspace_id, pipeline_key, status)",
            "create index idx_jobs_workspace_source on jobs(workspace_id, pipeline_key, source_type, source_id)",
        ),
        False,
    ),
    (
        "job_nodes",
        _JOB_NODES_TABLE_SQL,
        "job_nodes__v004",
        ("create index idx_job_nodes_job_status on job_nodes(job_id, status)",),
        True,
    ),
    (
        "node_runs",
        _NODE_RUNS_TABLE_SQL,
        "node_runs__v004",
        ("create index idx_node_runs_job_id on node_runs(job_id)",),
        False,
    ),
)

_EXECUTOR_LEASES = (
    "executor_leases",
    _EXECUTOR_LEASES_TABLE_SQL,
    "executor_leases__v004",
    (
        "create index idx_executor_leases_global_active on executor_leases(executor_id, status, expires_at)",
        "create index idx_executor_leases_workspace_active on executor_leases(workspace_id, executor_id, status, expires_at)",
        "create index idx_executor_leases_node_active on executor_leases(workspace_id, pipeline_key, node_key, status, expires_at)",
    ),
)


_MIGRATION_VERSION = 4
_MIGRATION_NAME = "workspace_dag_foreign_keys"

# V006 adds execution-control columns to jobs.  Ensure they are present on
# legacy tables before the V004 rebuild so source/replacement column sets match.
_JOB_EXECUTION_CONTROL_COLUMNS: tuple[tuple[str, str], ...] = (
    (
        "execution_mode",
        "text not null default 'full' check(execution_mode in ('full', 'until_node'))",
    ),
    ("target_node_key", "text"),
    ("execution_paused", "integer not null default 0 check(execution_paused in (0, 1))"),
    ("pause_reason", "text not null default ''"),
)


def _preflight_orphans(conn: sqlite3.Connection) -> None:
    """Raise before any destructive work if FK targets would be violated."""
    issues: list[MigrationIssue] = []

    for row in conn.execute(
        "select id, workspace_id from job_batches where workspace_id not in (select id from workspaces)"
    ).fetchall():
        issues.append(
            MigrationIssue(
                table="job_batches",
                row_key=row["id"],
                constraint="fk_job_batches_workspace_id",
                message=f"workspace_id '{row['workspace_id']}' does not exist",
            )
        )

    for row in conn.execute(
        "select id, workspace_id from jobs where workspace_id not in (select id from workspaces)"
    ).fetchall():
        issues.append(
            MigrationIssue(
                table="jobs",
                row_key=row["id"],
                constraint="fk_jobs_workspace_id",
                message=f"workspace_id '{row['workspace_id']}' does not exist",
            )
        )

    for row in conn.execute(
        "select id, job_id from job_nodes where job_id not in (select id from jobs)"
    ).fetchall():
        issues.append(
            MigrationIssue(
                table="job_nodes",
                row_key=str(row["id"]),
                constraint="fk_job_nodes_job_id",
                message=f"job_id '{row['job_id']}' does not exist",
            )
        )

    for row in conn.execute(
        "select id, job_id from node_runs where job_id not in (select id from jobs)"
    ).fetchall():
        issues.append(
            MigrationIssue(
                table="node_runs",
                row_key=str(row["id"]),
                constraint="fk_node_runs_job_id",
                message=f"job_id '{row['job_id']}' does not exist",
            )
        )

    for row in conn.execute(
        "select id, workspace_id from executor_leases where workspace_id not in (select id from workspaces)"
    ).fetchall():
        issues.append(
            MigrationIssue(
                table="executor_leases",
                row_key=row["id"],
                constraint="fk_executor_leases_workspace_id",
                message=f"workspace_id '{row['workspace_id']}' does not exist",
            )
        )

    for row in conn.execute(
        "select id, job_id from executor_leases where job_id not in (select id from jobs)"
    ).fetchall():
        issues.append(
            MigrationIssue(
                table="executor_leases",
                row_key=row["id"],
                constraint="fk_executor_leases_job_id",
                message=f"job_id '{row['job_id']}' does not exist",
            )
        )

    for row in conn.execute(
        "select id, node_run_id from executor_leases where node_run_id not in (select id from node_runs)"
    ).fetchall():
        issues.append(
            MigrationIssue(
                table="executor_leases",
                row_key=row["id"],
                constraint="fk_executor_leases_node_run_id",
                message=f"node_run_id '{row['node_run_id']}' does not exist",
            )
        )

    if issues:
        raise_blocked(
            MigrationReport(
                migration_version=_MIGRATION_VERSION,
                migration_name=_MIGRATION_NAME,
                issues=tuple(
                    sorted(
                        issues,
                        key=lambda issue: (issue.table, issue.row_key, issue.constraint),
                    )
                ),
            )
        )


def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return column names in table declaration order."""
    return [row["name"] for row in conn.execute(f"pragma table_info({table})").fetchall()]


def _copy_table(
    conn: sqlite3.Connection,
    source_table: str,
    replacement_table: str,
    create_sql: str,
    index_sqls: tuple[str, ...],
    drop_source: bool = True,
) -> None:
    """Copy ``source_table`` into a replacement table, validate, then swap.

    Steps:
    1. Create ``replacement_table`` with the supplied schema and real FKs.
    2. Copy every column explicitly from ``source_table``.
    3. Compare source and replacement row counts.
    4. Run ``pragma foreign_key_check('<replacement>')``.
    5. Drop ``source_table`` only after all checks pass (unless ``drop_source=False``,
       in which case it is renamed to ``<source_table>__v004_old``).
    6. Rename ``replacement_table`` to ``source_table``.
    7. Recreate explicit indexes and unique constraints.
    """
    conn.execute(create_sql)

    source_cols = _column_names(conn, source_table)
    replacement_cols = _column_names(conn, replacement_table)
    if set(source_cols) != set(replacement_cols):
        raise RuntimeError(
            f"{replacement_table} column mismatch with {source_table}: "
            f"{replacement_cols} vs {source_cols}"
        )

    col_list = ", ".join(source_cols)
    conn.execute(
        f"insert into {replacement_table} ({col_list}) select {col_list} from {source_table}"
    )

    source_count = conn.execute(f"select count(*) from {source_table}").fetchone()[0]
    replacement_count = conn.execute(f"select count(*) from {replacement_table}").fetchone()[0]
    if source_count != replacement_count:
        raise RuntimeError(
            f"{source_table} row count mismatch after copy: {replacement_count} vs {source_count}"
        )

    fk_violations = conn.execute(f"pragma foreign_key_check('{replacement_table}')").fetchall()
    if fk_violations:
        raise RuntimeError(
            f"foreign key check failed for {replacement_table}: "
            f"{'; '.join(str(row) for row in fk_violations)}"
        )

    _call_phase_hook(f"v004:copy:{source_table}")

    if drop_source:
        _call_phase_hook(f"v004:drop:{source_table}")
        conn.execute(f"drop table {source_table}")
    else:
        conn.execute(f"alter table {source_table} rename to {source_table}__v004_old")

    _call_phase_hook(f"v004:rename:{replacement_table}")
    conn.execute(f"alter table {replacement_table} rename to {source_table}")

    for idx_sql in index_sqls:
        conn.execute(idx_sql)


def _apply(conn: sqlite3.Connection) -> None:
    _preflight_orphans(conn)

    # Ensure V006 columns exist on legacy jobs tables before the rebuild.
    for column, ddl_fragment in _JOB_EXECUTION_CONTROL_COLUMNS:
        add_column_if_missing(
            conn,
            "jobs",
            column,
            f"alter table jobs add column {column} {ddl_fragment}",
        )

    # Rebuild tables.  jobs and node_runs keep their old copies because
    # executor_leases references them and must be rebuilt while the old
    # rows are still addressable.
    for source_table, create_sql, replacement_table, index_sqls, drop_source in _TABLES:
        _copy_table(
            conn, source_table, replacement_table, create_sql, index_sqls, drop_source=drop_source
        )

    # Rebuild executor_leases against the new jobs/node_runs tables, then
    # drop its old copy immediately.
    source_table, create_sql, replacement_table, index_sqls = _EXECUTOR_LEASES
    _copy_table(conn, source_table, replacement_table, create_sql, index_sqls)

    # Now safe to drop the kept old copies of jobs and node_runs.
    conn.execute("drop table if exists jobs__v004_old")
    conn.execute("drop table if exists node_runs__v004_old")


MIGRATION = Migration(
    version=_MIGRATION_VERSION,
    name=_MIGRATION_NAME,
    apply=_apply,
    rebuilds_fk=True,
)
