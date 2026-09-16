-- v82 / #659: append-and-fold counters with no blocking edge in AFTER triggers.
-- DELETE ... RETURNING atomically claims the visible delta set. A separate
-- SELECT then DELETE could erase a row committed between those statements
-- without ever adding it to the base count.

create table if not exists workspace_job_status_count_deltas (
  id bigserial primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  status text not null,
  delta bigint not null
);
create index if not exists idx_workspace_job_status_count_deltas_key
  on workspace_job_status_count_deltas(workspace_id, id);

create table if not exists run_job_status_count_deltas (
  id bigserial primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  run_id text not null,
  status text not null,
  delta bigint not null
);
create index if not exists idx_run_job_status_count_deltas_key
  on run_job_status_count_deltas(run_id, id);

create or replace function try_fold_workspace_job_status_counts(k text, lk int)
returns void as $$
declare
  st text;
  d bigint;
begin
  if not pg_try_advisory_xact_lock(82, lk) then
    return;
  end if;
  for st, d in
    with claimed as (
      delete from workspace_job_status_count_deltas
      where workspace_id = k returning status, delta
    )
    select status, sum(delta) from claimed group by status order by status
  loop
    if d < 0 then
      update workspace_job_status_counts set cnt = cnt + d
      where workspace_id = k and status = st;
    else
      insert into workspace_job_status_counts(workspace_id, status, cnt)
      values (k, st, d)
      on conflict (workspace_id, status)
      do update set cnt = workspace_job_status_counts.cnt + excluded.cnt;
    end if;
  end loop;
end;
$$ language plpgsql;

create or replace function try_fold_run_job_status_counts(
  k text, ws_lk int, run_lk int
) returns void as $$
declare
  st text;
  d bigint;
begin
  if not pg_try_advisory_xact_lock(82, ws_lk)
     or not pg_try_advisory_xact_lock(83, run_lk) then
    return;
  end if;
  for st, d in
    with claimed as (
      delete from run_job_status_count_deltas
      where run_id = k returning status, delta
    )
    select status, sum(delta) from claimed group by status order by status
  loop
    if d < 0 then
      update run_job_status_counts set cnt = cnt + d
      where run_id = k and status = st;
    else
      insert into run_job_status_counts(run_id, status, cnt)
      values (k, st, d)
      on conflict (run_id, status)
      do update set cnt = run_job_status_counts.cnt + excluded.cnt;
    end if;
  end loop;
end;
$$ language plpgsql;

create or replace function sync_workspace_job_status_counts() returns trigger as $$
declare
  k text;
  lk int;
begin
  if TG_OP = 'INSERT' then
    insert into workspace_job_status_count_deltas(workspace_id, status, delta)
    select workspace_id, status, count(*) from new_table group by 1, 2;
    for k, lk in
      select workspace_id, hashtext('ws:' || workspace_id)::int
      from new_table group by 1, 2 order by 2, 1
    loop
      perform try_fold_workspace_job_status_counts(k, lk);
    end loop;
  elsif TG_OP = 'DELETE' then
    -- A workspace cascade deletes the parent before this AFTER trigger runs.
    -- Skip its doomed negative deltas; ordinary job deletes still see and
    -- lock the parent through the delta table's immediate foreign key.
    insert into workspace_job_status_count_deltas(workspace_id, status, delta)
    select o.workspace_id, o.status, -count(*)::bigint
    from old_table o join workspaces w on w.id = o.workspace_id
    group by 1, 2;
    for k, lk in
      select o.workspace_id, hashtext('ws:' || o.workspace_id)::int
      from old_table o join workspaces w on w.id = o.workspace_id
      group by 1, 2 order by 2, 1
    loop
      perform try_fold_workspace_job_status_counts(k, lk);
    end loop;
  else
    insert into workspace_job_status_count_deltas(workspace_id, status, delta)
    select workspace_id, status, sum(cnt) from (
      select workspace_id, status, -count(*)::bigint as cnt
      from old_table group by 1, 2
      union all
      select workspace_id, status, count(*)::bigint
      from new_table group by 1, 2
    ) u group by workspace_id, status having sum(cnt) <> 0;
    for k, lk in
      select workspace_id, hashtext('ws:' || workspace_id)::int from (
        select workspace_id from new_table
        union
        select workspace_id from old_table
      ) keys order by 2, 1
    loop
      perform try_fold_workspace_job_status_counts(k, lk);
    end loop;
  end if;
  return null;
end;
$$ language plpgsql;

create or replace function sync_run_job_status_counts() returns trigger as $$
declare
  k text;
  ws_lk int;
  run_lk int;
begin
  if TG_OP = 'INSERT' then
    insert into run_job_status_count_deltas(workspace_id, run_id, status, delta)
    select workspace_id, run_id, status, count(*) from new_table
    where run_id <> '' group by 1, 2, 3;
    for k, ws_lk, run_lk in
      select run_id, hashtext('ws:' || workspace_id)::int,
             hashtext('run:' || run_id)::int
      from new_table where run_id <> '' group by 1, 2, 3 order by 2, 3, 1
    loop
      perform try_fold_run_job_status_counts(k, ws_lk, run_lk);
    end loop;
  elsif TG_OP = 'DELETE' then
    -- Same cascade guard as the workspace twin. Jobs with a real run_id hit
    -- this trigger first, so both families must filter the vanished parent.
    insert into run_job_status_count_deltas(workspace_id, run_id, status, delta)
    select o.workspace_id, o.run_id, o.status, -count(*)::bigint
    from old_table o join workspaces w on w.id = o.workspace_id
    where o.run_id <> '' group by 1, 2, 3;
    for k, ws_lk, run_lk in
      select o.run_id, hashtext('ws:' || o.workspace_id)::int,
             hashtext('run:' || o.run_id)::int
      from old_table o join workspaces w on w.id = o.workspace_id
      where o.run_id <> '' group by 1, 2, 3 order by 2, 3, 1
    loop
      perform try_fold_run_job_status_counts(k, ws_lk, run_lk);
    end loop;
  else
    insert into run_job_status_count_deltas(workspace_id, run_id, status, delta)
    select workspace_id, run_id, status, sum(cnt) from (
      select workspace_id, run_id, status, -count(*)::bigint as cnt
      from old_table where run_id <> '' group by 1, 2, 3
      union all
      select workspace_id, run_id, status, count(*)::bigint
      from new_table where run_id <> '' group by 1, 2, 3
    ) u group by workspace_id, run_id, status having sum(cnt) <> 0;
    for k, ws_lk, run_lk in
      select run_id, hashtext('ws:' || workspace_id)::int,
             hashtext('run:' || run_id)::int
      from (
        select workspace_id, run_id from new_table where run_id <> ''
        union
        select workspace_id, run_id from old_table where run_id <> ''
      ) keys order by 2, 3, 1
    loop
      perform try_fold_run_job_status_counts(k, ws_lk, run_lk);
    end loop;
  end if;
  return null;
end;
$$ language plpgsql;

drop trigger if exists jobs_run_status_counts_sync on jobs;
drop trigger if exists jobs_run_status_counts_sync_insert on jobs;
drop trigger if exists jobs_run_status_counts_sync_update on jobs;
drop trigger if exists jobs_run_status_counts_sync_delete on jobs;
create trigger jobs_run_status_counts_sync_insert
  after insert on jobs referencing new table as new_table
  for each statement execute function sync_run_job_status_counts();
create trigger jobs_run_status_counts_sync_update
  after update on jobs referencing new table as new_table old table as old_table
  for each statement execute function sync_run_job_status_counts();
create trigger jobs_run_status_counts_sync_delete
  after delete on jobs referencing old table as old_table
  for each statement execute function sync_run_job_status_counts();

drop trigger if exists jobs_status_counts_sync on jobs;
drop trigger if exists jobs_status_counts_sync_insert on jobs;
drop trigger if exists jobs_status_counts_sync_update on jobs;
drop trigger if exists jobs_status_counts_sync_delete on jobs;
create trigger jobs_status_counts_sync_insert
  after insert on jobs referencing new table as new_table
  for each statement execute function sync_workspace_job_status_counts();
create trigger jobs_status_counts_sync_update
  after update on jobs referencing new table as new_table old table as old_table
  for each statement execute function sync_workspace_job_status_counts();
create trigger jobs_status_counts_sync_delete
  after delete on jobs referencing old table as old_table
  for each statement execute function sync_workspace_job_status_counts();

create or replace function purge_run_job_status_counts() returns trigger as $$
begin
  delete from run_job_status_count_deltas where run_id = OLD.id;
  delete from run_job_status_counts where run_id = OLD.id;
  return OLD;
end;
$$ language plpgsql;
