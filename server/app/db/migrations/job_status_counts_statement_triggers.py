"""Schema v76: statement-level aggregation for the job status counters (#437).

The v36/v73 row triggers kept the counter tables transactionally in sync but
converged every job status transition of one run (or workspace) onto a
handful of (run_id, status) counter rows: each changed jobs row took its
counter row locks one row at a time, old-status row first then new-status
row, so the claim transaction's queued->running promote and the completion
path's running->completed flip touched the same few hot rows in different
orders at high frequency — a lock footprint that closed into deadlock rings
(psql DeadlockDetected / SQLSTATE 40P01 on the claim path's jobs promote)
under the high-concurrency tier: hundreds of workers claiming from single
runs with 10^4-scale items.

The v76 replacement is statement-level with transition tables: the trigger
fires once per statement, aggregates the NET delta per (key, status) from
the old/new transition tables (old side negative, new side positive), and
applies each delta exactly once in a FIXED global order — sorted by
(key, status) — through a plpgsql FOR loop. Two properties follow:

- Lock-order discipline: every statement, whatever mix of transitions it
  carries, takes its counter rows in the same sorted order, so no two
  concurrent statements can hold one row the other wants (the old shape's
  subtract-old-then-add-new two-arm order varied per transition and closed
  the ring; an unordered set-based apply cannot be relied on either — the
  planner is free to reorder subquery scans, verified experimentally on
  PG 17.11: set-based ORDER BY still deadlocked, the ordered FOR loop did
  not across the same probe grid).
- Batching: create_jobs_bulk's executemany insert of a 10^4-item run
  aggregates to one counter upsert per (run_id, status) per statement
  instead of one hot-row increment per jobs row.

Trigger shape notes (PG 17.11 verified): transition tables cannot be
combined with column lists nor with multi-event triggers, so each family is
three single-event AFTER triggers (INSERT / UPDATE / DELETE), and the
UPDATE arm is a plain ``after update`` — the net-delta arithmetic makes the
old ``update of status, run_id`` column filter redundant: a row whose
(key, status) did not change nets to zero and is filtered out of the delta
aggregation, so title/updated_at-only updates never touch the counters.

The trigger DDL lives HERE, not in postgres_schema.sql: the schema file's
raw-line budget (the same squeeze the v73 round resolved by moving DDL into
its migration, see 044d5cf2), and this shape must only run after the v73
run counters exist — which the version-sorted chain guarantees (v73 < v76)
on both the fresh path (every migration replays) and every upgrade path
(version > max(applied) replays). The schema file keeps only the counter
tables.
"""

from __future__ import annotations

from typing import Any

# Shared statement-level net-delta body. ``key`` is the counter dimension —
# ``run_id`` for run_job_status_counts, ``workspace_id`` for the workspace
# twin — spliced in by _trigger_ddl() so the two trigger families stay
# provably identical in shape (a single source for the lock-order fix).
#
# The UPDATE arm aggregates the union of the two transition tables into one
# net delta per (key, status) and applies the deltas in (key, status) order
# — the fixed global lock order is the deadlock fix itself (see module
# docstring). A negative net delta on a counter row that does not exist is
# dropped by the ON CONFLICT DO UPDATE shape (update branch matches
# nothing) exactly like the old row trigger's bare UPDATE did.
_BODY_TEMPLATE = """
create or replace function {fn}() returns trigger as $$
declare
  k text;
  st text;
  delta bigint;
begin
  if TG_OP = 'INSERT' then
    for k, st, delta in
      select {key}, status, count(*) from new_table where {key} <> ''
      group by 1, 2 order by 1, 2
    loop
      insert into {table}({key}, status, cnt)
      values (k, st, delta)
      on conflict ({key}, status)
      do update set cnt = {table}.cnt + excluded.cnt;
    end loop;
  elsif TG_OP = 'DELETE' then
    for k, st, delta in
      select {key}, status, -count(*)::bigint from old_table where {key} <> ''
      group by 1, 2 order by 1, 2
    loop
      update {table} set cnt = cnt + delta where {key} = k and status = st;
    end loop;
  else
    for k, st, delta in
      select key, status, sum(cnt) from (
        select {key} as key, status, -count(*)::bigint as cnt
        from old_table where {key} <> '' group by 1, 2
        union all
        select {key}, status, count(*)::bigint
        from new_table where {key} <> '' group by 1, 2
      ) u group by key, status order by key, status
    loop
      insert into {table}({key}, status, cnt)
      values (k, st, delta)
      on conflict ({key}, status)
      do update set cnt = {table}.cnt + excluded.cnt;
    end loop;
  end if;
  return null;
end;
$$ language plpgsql;
"""

_TRIGGER_TEMPLATE = """
drop trigger if exists {legacy_name} on jobs;
drop trigger if exists {insert_name} on jobs;
drop trigger if exists {update_name} on jobs;
drop trigger if exists {delete_name} on jobs;
create trigger {insert_name}
  after insert on jobs
  referencing new table as new_table
  for each statement execute function {fn}();
create trigger {update_name}
  after update on jobs
  referencing new table as new_table old table as old_table
  for each statement execute function {fn}();
create trigger {delete_name}
  after delete on jobs
  referencing old table as old_table
  for each statement execute function {fn}();
"""


def _trigger_ddl(*, fn: str, table: str, key: str, prefix: str, legacy_name: str) -> str:
    return _BODY_TEMPLATE.format(fn=fn, table=table, key=key) + _TRIGGER_TEMPLATE.format(
        fn=fn,
        legacy_name=legacy_name,
        insert_name=f"{prefix}_insert",
        update_name=f"{prefix}_update",
        delete_name=f"{prefix}_delete",
    )


# Run-level (v73, DB-RUN-JOB-STATUS-COUNTS-001): replaces the
# jobs_run_status_counts_sync row trigger created by the v73 migration.
_RUN_DDL = _trigger_ddl(
    fn="sync_run_job_status_counts",
    table="run_job_status_counts",
    key="run_id",
    prefix="jobs_run_status_counts_sync",
    legacy_name="jobs_run_status_counts_sync",
)

# Workspace-level twin (v36, DB-JOB-STATUS-COUNTS-001): replaces the
# jobs_status_counts_sync row trigger that postgres_schema.sql carried
# from v36 to v75. The workspace twin cannot skip rows on an empty key
# (workspace_id is never ''), so its filters drop the ``<> ''`` guard.
_WORKSPACE_DDL = _trigger_ddl(
    fn="sync_workspace_job_status_counts",
    table="workspace_job_status_counts",
    key="workspace_id",
    prefix="jobs_status_counts_sync",
    legacy_name="jobs_status_counts_sync",
).replace(" where workspace_id <> ''", "")


def migrate_job_status_counts_statement_triggers(conn: Any) -> None:
    """Replace the row-level job status count triggers (v36/v73) with
    statement-level transition-table aggregation (v76, #437)."""
    conn.execute(_RUN_DDL)
    conn.execute(_WORKSPACE_DDL)
