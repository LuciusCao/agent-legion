import sqlite3

from server.app.db.migrations.errors import MigrationError
from server.app.db.migrations.models import Migration

_JOBS_TABLE_SQL = """
create table jobs (
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
  foreign key(workspace_id) references workspaces(id) on delete cascade
)
"""

_JOB_BATCHES_TABLE_SQL = """
create table job_batches (
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

_JOB_NODES_TABLE_SQL = """
create table job_nodes (
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
create table node_runs (
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
create table executor_leases (
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


def _preflight_orphans(conn: sqlite3.Connection) -> None:
    """Raise before any destructive work if FK targets would be violated."""
    bad_batches = conn.execute(
        "select id from job_batches where workspace_id not in (select id from workspaces)"
    ).fetchall()
    bad_jobs = conn.execute(
        "select id from jobs where workspace_id not in (select id from workspaces)"
    ).fetchall()
    bad_nodes = conn.execute(
        "select id from job_nodes where job_id not in (select id from jobs)"
    ).fetchall()
    bad_runs = conn.execute(
        "select id from node_runs where job_id not in (select id from jobs)"
    ).fetchall()

    if bad_batches or bad_jobs or bad_nodes or bad_runs:
        details = []
        if bad_batches:
            details.append(f"job_batches: {','.join(sorted(r['id'] for r in bad_batches))}")
        if bad_jobs:
            details.append(f"jobs: {','.join(sorted(r['id'] for r in bad_jobs))}")
        if bad_nodes:
            details.append(f"job_nodes: {','.join(str(r['id']) for r in bad_nodes)}")
        if bad_runs:
            details.append(f"node_runs: {','.join(str(r['id']) for r in bad_runs)}")
        raise MigrationError(
            f"workspace DAG foreign key migration blocked by orphan rows: {'; '.join(details)}"
        )


def _drop_indexes_for_table(conn: sqlite3.Connection, table: str) -> None:
    """Drop non-automatic indexes attached to ``table`` before it is replaced."""
    rows = conn.execute(
        """
        select name from sqlite_master
        where type = 'index' and tbl_name = ? and name not like 'sqlite_autoindex_%'
        """,
        (table,),
    ).fetchall()
    for row in rows:
        conn.execute(f"drop index {row['name']}")


def _rebuild_table(
    conn: sqlite3.Connection,
    table: str,
    create_sql: str,
    index_sqls: tuple[str, ...],
    drop_old: bool = True,
) -> str:
    """Rebuild ``table`` with the schema in ``create_sql``.

    Returns the name of the old (renamed) table so callers can drop it after
    dependent tables have been rebuilt.
    """
    old = f"{table}__v004_old"
    conn.execute(f"alter table {table} rename to {old}")
    _drop_indexes_for_table(conn, old)
    conn.execute(create_sql)
    cols = [row["name"] for row in conn.execute(f"pragma table_info({table})").fetchall()]
    col_list = ", ".join(cols)
    conn.execute(f"insert into {table} ({col_list}) select {col_list} from {old}")

    old_count = conn.execute(f"select count(*) from {old}").fetchone()[0]
    new_count = conn.execute(f"select count(*) from {table}").fetchone()[0]
    if old_count != new_count:
        raise MigrationError(
            f"{table} row count mismatch after rebuild: {new_count} vs {old_count}"
        )

    for idx_sql in index_sqls:
        conn.execute(idx_sql)

    if drop_old:
        conn.execute(f"drop table {old}")
    return old


def _apply(conn: sqlite3.Connection) -> None:
    _preflight_orphans(conn)

    # Rebuild job_batches first; it has no dependent children.
    _rebuild_table(
        conn,
        "job_batches",
        _JOB_BATCHES_TABLE_SQL,
        ("create index idx_job_batches_workspace on job_batches(workspace_id, created_at)",),
    )

    # Rebuild jobs but keep the old copy until its children are rebuilt.
    jobs_old = _rebuild_table(
        conn,
        "jobs",
        _JOBS_TABLE_SQL,
        (
            "create index idx_jobs_pipeline_status on jobs(pipeline_key, status)",
            "create index idx_jobs_source on jobs(pipeline_key, source_type, source_id)",
            "create index idx_jobs_workspace_pipeline_status on jobs(workspace_id, pipeline_key, status)",
            "create index idx_jobs_workspace_source on jobs(workspace_id, pipeline_key, source_type, source_id)",
        ),
        drop_old=False,
    )

    # Rebuild children so they reference the new jobs table, then drop the old jobs table.
    _rebuild_table(
        conn,
        "job_nodes",
        _JOB_NODES_TABLE_SQL,
        ("create index idx_job_nodes_job_status on job_nodes(job_id, status)",),
    )
    _rebuild_table(
        conn,
        "node_runs",
        _NODE_RUNS_TABLE_SQL,
        ("create index idx_node_runs_job_id on node_runs(job_id)",),
    )
    _rebuild_table(
        conn,
        "executor_leases",
        _EXECUTOR_LEASES_TABLE_SQL,
        (
            "create index idx_executor_leases_global_active on executor_leases(executor_id, status, expires_at)",
            "create index idx_executor_leases_workspace_active on executor_leases(workspace_id, executor_id, status, expires_at)",
            "create index idx_executor_leases_node_active on executor_leases(workspace_id, pipeline_key, node_key, status, expires_at)",
        ),
    )
    conn.execute(f"drop table {jobs_old}")


MIGRATION = Migration(
    version=4,
    name="workspace_dag_foreign_keys",
    apply=_apply,
    rebuilds_fk=True,
)
