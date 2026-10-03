"""The pre-v88 (v70–v87) ``bump_job_node_status_counts`` body (#690).

Verbatim from the v87 postgres_schema.sql: a direct per-call upsert on the
shared (workspace_id, node_key, status) counter row. Two consumers rewind to
it — the schema-parity undo step (a faithful v87 database) and the #690
deadlock control arm (proving the regression tests reproduce the ring
against the old shape, not just pass against the new one).
"""

from __future__ import annotations

PRE_V88_BUMP_SQL = """
create or replace function bump_job_node_status_counts(
  p_workspace_id text, p_node_key text, p_status text, p_delta bigint
) returns void as $$
begin
  if p_delta > 0 then
    insert into workspace_job_node_status_counts(workspace_id, node_key, status, cnt)
    values (p_workspace_id, p_node_key, p_status, p_delta)
    on conflict (workspace_id, node_key, status)
    do update set cnt = workspace_job_node_status_counts.cnt + p_delta;
  else
    update workspace_job_node_status_counts set cnt = cnt + p_delta
    where workspace_id = p_workspace_id
      and node_key = p_node_key and status = p_status;
  end if;
end;
$$ language plpgsql
"""
