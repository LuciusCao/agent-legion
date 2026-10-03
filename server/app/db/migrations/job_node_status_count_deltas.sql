-- v88 / #690: the job NODE counter family gets v82's append-and-fold shape.
-- Every counter write enters through bump_job_node_status_counts (the row
-- trigger on job_nodes and the jobs deduct/rekey triggers in
-- postgres_schema.sql all delegate to it). Nothing here ever waits:
-- pg_try_advisory_xact_lock(88, ws) elects the one transaction allowed to
-- write a workspace's base rows until it ends; every other writer appends an
-- insert-only delta row (bigserial key, no business-key uniqueness, so no
-- "inserting index tuple" wait either). Readers sum base + committed deltas
-- in one snapshot, exact on either side of a fold commit.

create table if not exists workspace_job_node_status_count_deltas (
  id bigserial primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  node_key text not null,
  status text not null,
  delta bigint not null
);
create index if not exists idx_workspace_job_node_status_count_deltas_key
  on workspace_job_node_status_count_deltas(workspace_id, id);

-- Base write, only ever called by the class-88 holder of workspace k. The
-- sign split keeps the pre-v88 missing-row semantics: a positive delta
-- upserts, a negative one is a bare UPDATE (no-op on a missing row, never a
-- negative phantom row).
create or replace function apply_job_node_status_count(
  k text, nk text, st text, d bigint
) returns void as $$
begin
  if d > 0 then
    insert into workspace_job_node_status_counts(workspace_id, node_key, status, cnt)
    values (k, nk, st, d)
    on conflict (workspace_id, node_key, status)
    do update set cnt = workspace_job_node_status_counts.cnt + excluded.cnt;
  elsif d < 0 then
    update workspace_job_node_status_counts set cnt = cnt + d
    where workspace_id = k and node_key = nk and status = st;
  end if;
end;
$$ language plpgsql;

-- Try to become (or stay — xact advisory locks are re-entrant) workspace k's
-- folder; a winner atomically claims the visible committed delta tail with
-- DELETE ... RETURNING (a separate SELECT then DELETE could erase a row
-- committed in between without adding it) and folds it into the base.
-- Class 88 is disjoint from v82's job-family classes 82/83: the two families
-- elect folders independently, and since neither ever blocks, no acquisition
-- order between them exists to get wrong.
create or replace function try_fold_job_node_status_counts(k text)
returns boolean as $$
declare
  nk text;
  st text;
  d bigint;
begin
  if not pg_try_advisory_xact_lock(88, hashtext('ws:' || k)) then
    return false;
  end if;
  for nk, st, d in
    with claimed as (
      delete from workspace_job_node_status_count_deltas
      where workspace_id = k returning node_key, status, delta
    )
    select node_key, status, sum(delta) from claimed
    group by 1, 2 order by 1, 2
  loop
    perform apply_job_node_status_count(k, nk, st, d);
  end loop;
  return true;
end;
$$ language plpgsql;

-- The single counter-write entry. The fold runs BEFORE the winner's own
-- write: a committed +1 still pending as a delta must reach the base before
-- this transaction's -1 on the same (node_key, status), or the bare-UPDATE
-- arm would drop the decrement on a not-yet-materialized row.
create or replace function bump_job_node_status_counts(
  p_workspace_id text, p_node_key text, p_status text, p_delta bigint
) returns void as $$
begin
  if try_fold_job_node_status_counts(p_workspace_id) then
    perform apply_job_node_status_count(p_workspace_id, p_node_key, p_status, p_delta);
  else
    insert into workspace_job_node_status_count_deltas(workspace_id, node_key, status, delta)
    values (p_workspace_id, p_node_key, p_status, p_delta);
  end if;
end;
$$ language plpgsql;
