"""Schema v82: per-key advisory locks for the job status count triggers (#659).

The v77 statement-level rebuild (#437) fixed the ring WITHIN one statement:
every firing applies its net deltas in a fixed (key, status) sorted order.
What it could not fix is the ring ACROSS statements inside ONE transaction —
the production shape of #659: a claim batch promotes several executions in
one transaction, and psycopg executemany issues each promote as its own
UPDATE statement, so the transaction fires the counter trigger several
times, each firing taking its (key, status) row locks in sorted order but
the SEQUENCE of row-lock sets varying with the business order of the
promotes. Two such multi-statement transactions interleaving (claim batch
vs. rerun's mark_nodes_for_rerun, both touching one workspace's hot counter
rows) close an AB-BA ring on the counter rows: PG's deadlock detector
breaks it after 1s, the client retries, and the visible symptoms are the
claim/heartbeat/rerun 500 waves plus the result-commit 409 waves (the first
POST commits but its response is lost in the lock queue; the retried POST
hits the terminal state — the 409 is a downstream symptom of this lock
contention, not an independent bug).

The fix is issue #659's direction 1, minimal and structural: the trigger
takes transaction-scoped advisory locks BEFORE touching any counter row,
in a TWO-LEVEL hierarchy (codex review round): every trigger of BOTH
families locks the workspace-level key (``ws:<workspace_id>``) for every
distinct workspace in its transition tables FIRST, then its own dimension
keys (``run:<run_id>`` for the run twin; the workspace twin's dimension
lock is the same ws: keyspace — re-entrant, free). PostgreSQL fires
same-table triggers in name order — ``jobs_run_*`` before ``jobs_status_*``
— so a per-family-only lock order left a cross-family ring: transaction A
(run trigger fires first) could hold run:a + ws:x while B held run:b
waiting on ws:x, and A's NEXT statement's run trigger would wait on run:b.
The global hierarchy (ws: before run:, both sorted) makes every writer's
acquisition sequence consistent regardless of statement mix or family
order. Properties relied upon: advisory locks for the job status count triggers (#659).

The v77 statement-level rebuild (#437) fixed the ring WITHIN one statement:
every firing applies its net deltas in a fixed (key, status) sorted order.
What it could not fix is the ring ACROSS statements inside ONE transaction —
the production shape of #659: a claim batch promotes several executions in
one transaction, and psycopg executemany issues each promote as its own
UPDATE statement, so the transaction fires the counter trigger several
times, each firing taking its (key, status) row locks in sorted order but
the SEQUENCE of row-lock sets varying with the business order of the
promotes. Two such multi-statement transactions interleaving (claim batch
vs. rerun's mark_nodes_for_rerun, both touching one workspace's hot counter
rows) close an AB-BA ring on the counter rows: PG's deadlock detector
breaks it after 1s, the client retries, and the visible symptoms are the
claim/heartbeat/rerun 500 waves plus the result-commit 409 waves (the first
POST commits but its response is lost in the lock queue; the retried POST
hits the terminal state — the 409 is a downstream symptom of this lock
contention, not an independent bug).

The fix is issue #659's direction 1, minimal and structural: the trigger
takes a per-key transaction-scoped advisory lock BEFORE touching any
counter row. ``pg_advisory_xact_lock`` on the distinct keys of the
statement, taken in sorted key order at function entry, serialises ALL
counter writers per key — once a writer holds the key's advisory lock, no
other writer can be mid-loop on that key's rows, so the sorted per-statement
loop order becomes irrelevant to ring formation (there is never a second
concurrent lock holder to interleave with). Properties relied on:

- Re-entrancy: pg_advisory_xact_lock is re-entrant within a transaction —
  the same key locked by a second firing of the same transaction (the
  multi-statement claim batch) acquires instantly, and all locks release
  automatically at commit/rollback. A subtransaction ROLLBACK TO SAVEPOINT
  does NOT release it (inherited from the enclosing transaction) — safe
  here: the counter writes before the savepoint roll back with the
  savepoint, and the lock's only job is ordering between transactions.
- Keyspace isolation: the lock key is ``hashtext('<family>:<key>')`` —
  ``'run:' || run_id`` for the run twin, ``'ws:' || workspace_id`` for the
  workspace twin — so the two families never cross-lock even where a run
  id and a workspace id share text, and neither collides with the other
  pg_advisory_xact_lock users in this code base (they hash their own
  prefixed key strings). hashtext is 32-bit: collisions between distinct
  keys only cause harmless EXTRA serialisation (two writers queue behind
  one advisory lock they did not strictly need to share), never a missed
  serialisation — the counter rows themselves still arbitrate correctness.
- Critical section: the serialised region is the net-delta aggregation
  (in-memory) plus one targeted write per (key, status) — microseconds;
  no measurable throughput cost at current volumes.

Also tightened in the same rebuild: the DELETE arm's group-by select gains
the ``order by 1, 2`` the INSERT/UPDATE arms already had (the v77 module
missed it) — with the advisory lock per key this is belt-and-braces, but
the discipline (every loop that takes counter row locks walks a fixed
order) stays uniform across the three arms.

Delta semantics are byte-identical to v77 (sign-split apply, zero-net
filter, ``<> ''`` guard on the run twin) — the module splices the same
template with the added prologue, so the two trigger families stay
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
      perform pg_advisory_xact_lock(hashtext('ws:' || lk));
    end loop;
    for lk in select distinct {key} from new_table where {key} <> '' order by 1 loop
      perform pg_advisory_xact_lock(hashtext('{key_prefix}' || lk));
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
      perform pg_advisory_xact_lock(hashtext('ws:' || lk));
    end loop;
    for lk in select distinct {key} from old_table where {key} <> '' order by 1 loop
      perform pg_advisory_xact_lock(hashtext('{key_prefix}' || lk));
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
      perform pg_advisory_xact_lock(hashtext('ws:' || lk));
    end loop;
    for lk in
      select distinct key from (
        select {key} as key from old_table where {key} <> ''
        union
        select {key} from new_table where {key} <> ''
      ) lk_keys order by 1
    loop
      perform pg_advisory_xact_lock(hashtext('{key_prefix}' || lk));
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
