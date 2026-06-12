import sqlite3

from server.app.db.migrations.runner import Migration


def _apply(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table workspace_executor_allocations (
          workspace_id text not null,
          executor_id text not null,
          concurrency_limit integer not null check(concurrency_limit > 0),
          primary key(workspace_id, executor_id),
          foreign key(workspace_id) references workspaces(id) on delete cascade
        )
        """
    )
    conn.execute(
        """
        create table workspace_node_bindings (
          workspace_id text not null,
          pipeline_key text not null,
          node_key text not null,
          executor_id text not null,
          primary key(workspace_id, pipeline_key, node_key),
          foreign key(workspace_id) references workspaces(id) on delete cascade
        )
        """
    )
    conn.execute(
        """
        create table workspace_node_limits (
          workspace_id text not null,
          pipeline_key text not null,
          node_key text not null,
          concurrency_limit integer not null check(concurrency_limit > 0),
          primary key(workspace_id, pipeline_key, node_key),
          foreign key(workspace_id) references workspaces(id) on delete cascade
        )
        """
    )
    conn.execute(
        """
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
    )
    conn.execute(
        """
        create index if not exists idx_executor_leases_global_active
          on executor_leases(executor_id, status, expires_at)
        """
    )
    conn.execute(
        """
        create index if not exists idx_executor_leases_workspace_active
          on executor_leases(workspace_id, executor_id, status, expires_at)
        """
    )
    conn.execute(
        """
        create index if not exists idx_executor_leases_node_active
          on executor_leases(workspace_id, pipeline_key, node_key, status, expires_at)
        """
    )


MIGRATION = Migration(version=1, name="executor_core", apply=_apply)
