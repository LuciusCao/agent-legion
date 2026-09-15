"""Schema v82: two-level advisory locks for the job status count triggers (#659).

The problem (production, #659): the jobs-table counter triggers fire inside
the SAME transaction as claim/result/rerun writes. The v77 statement-level
rebuild (#437) fixed the ring WITHIN one statement — every firing applies
its net deltas in a fixed (key, status) sorted order. It could not fix the
rings ACROSS statements (a claim batch promotes several executions as
separate UPDATE statements; the transaction fires the counter trigger once
per statement, so the SEQUENCE of lock sets varies with business order) nor
ACROSS trigger families (PostgreSQL fires same-table triggers in name
order — ``jobs_run_*`` before ``jobs_status_*`` — so the run twin's locks
always precede the workspace twin's within one statement). Two interleaving
multi-statement transactions close AB-BA rings on the counter rows; PG's
detector breaks each after ~1s, the client retries, and the visible
symptoms were the claim/heartbeat/rerun 500 waves plus the result-commit
409 waves (first POST commits, response lost in the lock queue; the
retried POST hits the terminal state — a downstream symptom, not a bug).

The fix (issue direction 1, structural): every trigger of BOTH families
takes transaction-scoped advisory locks BEFORE touching any counter row,
in a TWO-LEVEL hierarchy —

1. workspace level: ``pg_advisory_xact_lock(82, hashtext('ws:' ||
   <workspace_id>))`` for every distinct workspace in the statement's
   transition tables, sorted;
2. dimension level: ``pg_advisory_xact_lock(82, hashtext('<family>:'
   || <key>))`` for every distinct counter key, sorted (``run:`` for the
   run twin; the workspace twin's dimension key IS the ws: keyspace —
   re-entrant, free).

Because every writer — whatever its statement mix, business order, or
family firing order — takes the ws: level before any dimension key of that
workspace, a "holds run:r while waiting on ws:x" state is unreachable
(anyone holding run:r passed ws:x first), and same-workspace writers
serialise at the ws: gate before any counter row or dimension lock. The
two-int lock class id 82 dedicates the keyspace to this migration: the
only other two-int advisory user in the codebase (the studio
publish-request handshake) uses class id 416429, so class 82 is exclusive
to the counter triggers — a future two-int user must pick a different id
(pinned by the shape test).

Properties relied upon:

- Re-entrancy: pg_advisory_xact_lock is re-entrant within a transaction —
  a second firing of the same transaction (the multi-statement claim
  batch) acquires instantly, and all locks release at commit/rollback.
  A subtransaction ROLLBACK TO SAVEPOINT does NOT release them
  (inherited from the enclosing transaction) — safe here: the counter
  writes before the savepoint roll back with the savepoint, and the
  lock's only job is ordering between transactions.
- Residual window (known, accepted): the hierarchy orders locks WITHIN
  one statement. A transaction whose successive statements touch
  DIFFERENT workspaces in different orders (e.g. a sweeper processing
  scanned rows unordered across workspaces vs. a claim batch walking
  EXEC-CLAIM-LOCK-001's ascending order) can still ring cross-workspace.
  #659's production shapes are same-workspace; the detector plus the
  claim/heartbeat retry liveness absorb the residual. Cross-workspace
  multi-statement jobs DML follows the ascending-workspace discipline:
  all five sweep paths (broker claim sweep, stale-definition sweep,
  unclaimable-model sweep, lease expiry, orphaned-job recovery) sort
  their rows by workspace_id.
- All three arms' delta loops keep v77's ``order by`` unchanged
  (v77 already sorted every arm's group-by select); the new lock loops
  follow the same fixed-order discipline.

Delta semantics are byte-identical to v77 (sign-split apply, zero-net
filter, ``<> ''`` guard on the run twin) — the module splices the same
template with the added prologues, so the two trigger families stay
provably identical in shape. v77 itself stays untouched: deployed
databases already recorded it, and this migration replaces the functions
wholesale (create or replace function) on fresh AND upgrade paths (the
version-sorted chain replays v82 after v77 everywhere).

The trigger DDL lives HERE, not in postgres_schema.sql, for the same
reasons as v77 (the schema file's raw-line budget; the shape must only
exist after the v73/v36 counter tables and the v77 trigger shape).
"""

from __future__ import annotations

from typing import Any

from server.app.db.migrations.job_status_counts_statement_triggers import (
    _TRIGGER_TEMPLATE,
)

# The v77 net-delta body (see that module for the sign-split semantics this
# rebuild must preserve exactly) with the advisory-lock prologue spliced in
# ahead of every branch: lock every DISTINCT key carried by the statement's
# transition tables — both sides for the UPDATE arm, the relevant side for
# INSERT/DELETE — in sorted key order, THEN walk the existing delta loops.
# The lock prefix keyspace-splits the two families (``run:`` vs ``ws:``);
# pg_advisory_xact_lock is re-entrant per transaction and released at
# commit/rollback. ``{key_prefix}`` splices in the family prefix, ``{key}``
# the counter dimension, ``{table}`` the counter table (v77 spelling).
_BODY_TEMPLATE = """
create or replace function {fn}() returns trigger as $$
declare
  k text;
  st text;
  delta bigint;
  lk text;
begin
  -- Lock hierarchy (review P1): workspace-level advisory locks FIRST, then
  -- the counter-dimension keys — in BOTH trigger families. PostgreSQL fires
  -- same-table triggers in name order (jobs_run_* before jobs_status_*), so
  -- without this hierarchy a multi-statement transaction could hold run:a +
  -- ws:x while another holds run:b and waits on ws:x, and the first's next
  -- statement waits on run:b — a cross-family AB-BA ring that no per-family
  -- sorted order can break. Every trigger taking ws:<workspace> before any
  -- run:<run> key (and the workspace twin taking only ws: keys) makes the
  -- acquisition order globally consistent: workspace level, then dimension
  -- level, both sorted.
  if TG_OP = 'INSERT' then
    for lk in select distinct workspace_id from new_table order by 1 loop
      perform pg_advisory_xact_lock(82, hashtext('ws:' || lk));
    end loop;
    for lk in select distinct {key} from new_table where {key} <> '' order by 1 loop
      perform pg_advisory_xact_lock(82, hashtext('{key_prefix}' || lk));
    end loop;
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
    for lk in select distinct workspace_id from old_table order by 1 loop
      perform pg_advisory_xact_lock(82, hashtext('ws:' || lk));
    end loop;
    for lk in select distinct {key} from old_table where {key} <> '' order by 1 loop
      perform pg_advisory_xact_lock(82, hashtext('{key_prefix}' || lk));
    end loop;
    for k, st, delta in
      select {key}, status, -count(*)::bigint from old_table where {key} <> ''
      group by 1, 2 order by 1, 2
    loop
      update {table} set cnt = cnt + delta where {key} = k and status = st;
    end loop;
  else
    for lk in
      select distinct workspace_id from (
        select workspace_id from old_table
        union
        select workspace_id from new_table
      ) ws_keys order by 1
    loop
      perform pg_advisory_xact_lock(82, hashtext('ws:' || lk));
    end loop;
    for lk in
      select distinct key from (
        select {key} as key from old_table where {key} <> ''
        union
        select {key} from new_table where {key} <> ''
      ) lk_keys order by 1
    loop
      perform pg_advisory_xact_lock(82, hashtext('{key_prefix}' || lk));
    end loop;
    for k, st, delta in
      select key, status, sum(cnt) from (
        select {key} as key, status, -count(*)::bigint as cnt
        from old_table where {key} <> '' group by 1, 2
        union all
        select {key}, status, count(*)::bigint
        from new_table where {key} <> '' group by 1, 2
      ) u group by key, status having sum(cnt) <> 0 order by key, status
    loop
      if delta < 0 then
        update {table} set cnt = cnt + delta where {key} = k and status = st;
      else
        insert into {table}({key}, status, cnt)
        values (k, st, delta)
        on conflict ({key}, status)
        do update set cnt = {table}.cnt + excluded.cnt;
      end if;
    end loop;
  end if;
  return null;
end;
$$ language plpgsql;
"""

# v77's trigger shell (drop legacy + three single-event transition-table
# triggers) is reused verbatim: the migration re-CREATES the same trigger
# names against the replaced function bodies, so deployed and fresh
# databases converge on identical shapes (the parity test's catalog diff
# covers both paths).


def _advisory_trigger_ddl(
    *, fn: str, table: str, key: str, key_prefix: str, prefix: str, legacy_name: str
) -> str:
    return _BODY_TEMPLATE.format(fn=fn, table=table, key=key, key_prefix=key_prefix) + (
        _TRIGGER_TEMPLATE.format(
            fn=fn,
            legacy_name=legacy_name,
            insert_name=f"{prefix}_insert",
            update_name=f"{prefix}_update",
            delete_name=f"{prefix}_delete",
        )
    )


# Run-level (v73/v77): same function/table/key spelling as v77's _RUN_DDL.
_RUN_DDL = _advisory_trigger_ddl(
    fn="sync_run_job_status_counts",
    table="run_job_status_counts",
    key="run_id",
    key_prefix="run:",
    prefix="jobs_run_status_counts_sync",
    legacy_name="jobs_run_status_counts_sync",
)

# Workspace-level twin: v77's ``.replace(" where workspace_id <> ''", "")``
# drops the empty-key guard (workspace_id is never '') — same splice here,
# and the guard-free SELECT DISTINCT collapses to a plain scan.
_WORKSPACE_DDL = _advisory_trigger_ddl(
    fn="sync_workspace_job_status_counts",
    table="workspace_job_status_counts",
    key="workspace_id",
    key_prefix="ws:",
    prefix="jobs_status_counts_sync",
    legacy_name="jobs_status_counts_sync",
).replace(" where workspace_id <> ''", "")


def migrate_job_status_counts_advisory_locks(conn: Any) -> None:
    """Re-create the job status count trigger functions with per-key
    transaction-scoped advisory locks (v82, #659)."""
    conn.execute(_RUN_DDL)
    conn.execute(_WORKSPACE_DDL)
