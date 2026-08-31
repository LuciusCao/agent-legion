"""Pre-v70 schema shape restoration for workflow_key migration tests.

The v68 alignment migration still runs with full effect on real pre-v68→v70
upgrades (old installs keep their ``workflow_key`` columns through the schema
replay), so its row-surgery tests must exercise it against the v69 shape —
not skip. ``restore_pre_v70_shape`` re-widens a current-shape database: the
columns come back with their historical ``not null default ''`` DDL, the
four composite-PK tables regain their three-column keys, the revision unique
returns to ``(workspace_id, workflow_key, version)``, and the node-status
trigger chain returns to its five-parameter form (the terminal four-parameter
replay cannot write the keyed rows these scenarios need).

Everything is idempotent across repeated tests on the shared worktree
database: drop-if-exists precedes each restore, and ``init_db`` replays never
remove what an earlier restore added.
"""

from __future__ import annotations

from typing import Any

_REVISIONS_UNIQUE_OLD = "workflow_revisions_workspace_id_workflow_key_version_key"
_REVISIONS_UNIQUE_V70 = "workflow_revisions_workspace_id_version_key"

_RESTORE_COLUMN_TABLES = (
    "runs",
    "jobs",
    "executor_leases",
    "agent_execution_requests",
    "workflow_revisions",
)
_RESTORE_PK_TABLES = (
    ("workspace_node_limits", "(workspace_id, workflow_key, node_key)"),
    ("workspace_node_routes", "(workspace_id, workflow_key, node_key)"),
    ("workspace_node_capacities", "(workspace_id, workflow_key, node_key)"),
    ("workspace_job_node_status_counts", "(workspace_id, workflow_key, node_key, status)"),
)

# v69-era node-status trigger chain (five-parameter bump; keyed deduct and
# rekey). Verbatim from the pre-M2 postgres_schema.sql so the simulated v69
# maintains counts exactly like the real one.
_BUMP_FN_V69 = """
create or replace function bump_job_node_status_counts(
  p_workspace_id text, p_workflow_key text, p_node_key text, p_status text, p_delta bigint
) returns void as $$
begin
  if p_delta > 0 then
    insert into workspace_job_node_status_counts(workspace_id, workflow_key, node_key, status, cnt)
    values (p_workspace_id, p_workflow_key, p_node_key, p_status, p_delta)
    on conflict (workspace_id, workflow_key, node_key, status)
    do update set cnt = workspace_job_node_status_counts.cnt + p_delta;
  else
    update workspace_job_node_status_counts set cnt = cnt + p_delta
    where workspace_id = p_workspace_id and workflow_key = p_workflow_key
      and node_key = p_node_key and status = p_status;
  end if;
end;
$$ language plpgsql;
"""

_SYNC_FN_V69 = """
create or replace function sync_job_node_status_counts() returns trigger as $$
declare
  parent_ws text;
  parent_wf text;
begin
  if TG_OP = 'INSERT' then
    select workspace_id, workflow_key into parent_ws, parent_wf
      from jobs where id = NEW.job_id;
    if found then
      perform bump_job_node_status_counts(parent_ws, parent_wf, NEW.node_key, NEW.status, 1);
    end if;
    return NEW;
  elsif TG_OP = 'DELETE' then
    select workspace_id, workflow_key into parent_ws, parent_wf
      from jobs where id = OLD.job_id;
    if found then
      perform bump_job_node_status_counts(parent_ws, parent_wf, OLD.node_key, OLD.status, -1);
    end if;
    return OLD;
  else
    if NEW.job_id is distinct from OLD.job_id
       or NEW.node_key is distinct from OLD.node_key
       or NEW.status is distinct from OLD.status then
      select workspace_id, workflow_key into parent_ws, parent_wf
        from jobs where id = OLD.job_id;
      if found then
        perform bump_job_node_status_counts(parent_ws, parent_wf, OLD.node_key, OLD.status, -1);
      end if;
      select workspace_id, workflow_key into parent_ws, parent_wf
        from jobs where id = NEW.job_id;
      if found then
        perform bump_job_node_status_counts(parent_ws, parent_wf, NEW.node_key, NEW.status, 1);
      end if;
    end if;
    return NEW;
  end if;
end;
$$ language plpgsql;
"""

_DEDUCT_FN_V69 = """
create or replace function deduct_job_node_status_counts() returns trigger as $$
begin
  update workspace_job_node_status_counts c set cnt = c.cnt - s.cnt
  from (
    select node_key, status, count(*) as cnt from job_nodes
    where job_id = OLD.id group by 1, 2
  ) s
  where c.workspace_id = OLD.workspace_id and c.workflow_key = OLD.workflow_key
    and c.node_key = s.node_key and c.status = s.status;
  return OLD;
end;
$$ language plpgsql;
"""

_REKEY_FN_V69 = """
create or replace function rekey_job_node_status_counts() returns trigger as $$
begin
  update workspace_job_node_status_counts c set cnt = c.cnt - s.cnt
  from (
    select node_key, status, count(*) as cnt from job_nodes
    where job_id = OLD.id group by 1, 2
  ) s
  where c.workspace_id = OLD.workspace_id and c.workflow_key = OLD.workflow_key
    and c.node_key = s.node_key and c.status = s.status;
  insert into workspace_job_node_status_counts(workspace_id, workflow_key, node_key, status, cnt)
  select NEW.workspace_id, NEW.workflow_key, node_key, status, count(*)
  from job_nodes where job_id = NEW.id group by 3, 4
  on conflict (workspace_id, workflow_key, node_key, status)
  do update set cnt = workspace_job_node_status_counts.cnt + excluded.cnt;
  return NEW;
end;
$$ language plpgsql;
"""

_REKEY_TRIGGER_V69 = """
drop trigger if exists jobs_node_status_counts_rekey on jobs;
create trigger jobs_node_status_counts_rekey
  after update of workspace_id, workflow_key on jobs
  for each row execute function rekey_job_node_status_counts();
"""


def restore_pre_v70_shape(conn: Any) -> None:
    """Re-widen a current-shape database to the v69 shape (idempotent)."""
    conn.execute(
        f"alter table workflow_revisions drop constraint if exists {_REVISIONS_UNIQUE_V70}"
    )
    conn.execute(
        f"alter table workflow_revisions drop constraint if exists {_REVISIONS_UNIQUE_OLD}"
    )
    for table in _RESTORE_COLUMN_TABLES:
        conn.execute(
            f"alter table {table} add column if not exists workflow_key text not null default ''"
        )
        conn.execute(f"update {table} set workflow_key=workspace_id")
    conn.execute(
        f"alter table workflow_revisions add constraint {_REVISIONS_UNIQUE_OLD}"
        " unique (workspace_id, workflow_key, version)"
    )
    for table, pk in _RESTORE_PK_TABLES:
        conn.execute(
            f"alter table {table} add column if not exists workflow_key text not null default ''"
        )
        conn.execute(f"update {table} set workflow_key=workspace_id")
        conn.execute(f"alter table {table} drop constraint if exists {table}_pkey")
        conn.execute(f"alter table {table} add primary key {pk}")
    conn.execute(_BUMP_FN_V69)
    conn.execute(_SYNC_FN_V69)
    conn.execute(_DEDUCT_FN_V69)
    conn.execute(_REKEY_FN_V69)
    conn.execute(_REKEY_TRIGGER_V69)


def narrow_back_to_v70(conn: Any) -> None:
    """Undo ``restore_pre_v70_shape``: replay the v70 migration's DDL so
    later tests on the shared database see the terminal shape (baseline
    assertions key on the exact column set). The v69 rekey trigger drops
    first — it depends on the column — and the per-test ``init_db`` replay
    recreates the terminal trigger chain."""
    conn.execute("drop trigger if exists jobs_node_status_counts_rekey on jobs")
    conn.execute(
        f"alter table workflow_revisions drop constraint if exists {_REVISIONS_UNIQUE_OLD}"
    )
    conn.execute(
        f"alter table workflow_revisions drop constraint if exists {_REVISIONS_UNIQUE_V70}"
    )
    for table, _pk in _RESTORE_PK_TABLES:
        conn.execute(f"alter table {table} drop constraint if exists {table}_pkey")
    for table in _RESTORE_COLUMN_TABLES + tuple(t for t, _ in _RESTORE_PK_TABLES):
        conn.execute(f"alter table {table} drop column if exists workflow_key")
    conn.execute(
        f"alter table workflow_revisions add constraint {_REVISIONS_UNIQUE_V70}"
        " unique (workspace_id, version)"
    )
    for table, pk in (
        ("workspace_node_limits", "(workspace_id, node_key)"),
        ("workspace_node_routes", "(workspace_id, node_key)"),
        ("workspace_node_capacities", "(workspace_id, node_key)"),
        ("workspace_job_node_status_counts", "(workspace_id, node_key, status)"),
    ):
        conn.execute(f"alter table {table} add primary key {pk}")
    conn.execute(
        "create index if not exists idx_workflow_revisions_active"
        " on workflow_revisions(workspace_id, status)"
    )
    conn.execute(
        "create index if not exists idx_agent_requests_node_active"
        " on agent_execution_requests(workspace_id, node_key, state)"
    )
