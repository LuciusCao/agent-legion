"""Schema v101 (#946): job batch operations async queueing — 批量操作任务行。

Phase 4 of the runs-async milestone (docs/architecture/batch-ops-async.md):
the 8 batch job endpoints (rerun / delete / upgrade / run-to / pause / resume
/ package / clear-packed / rerun-by-failure) enqueue selections above the
sync threshold instead of holding the request threadpool slot. One
``job_batch_operations`` row per accepted operation holds the frozen target
spec (normalized job ids + kind params), the cursor, the counters, and the
feeder state machine — the ``run_batches`` shape (v99, #1117) mapped from
"create jobs" onto "mutate existing jobs". Per-job results live in the
``job_batch_operation_results`` child table (chunked inserts, one row per
job) so status polling never ships MB-scale JSON and the results endpoint
replays the sync per-job contract verbatim.

Bookkeeping tables, NOT the EXEC-GENERATION-002 pinned families (execution
state / artifact bytes / manifest rows): the per-job mutations still execute
through the existing services' registered write surfaces, so the registry
needs no new entries (the run_batches precedent).

The DDL rides the apply fn (postgres_schema.sql sits at its raw-line ceiling;
v76/v96/v99 precedent): fresh and pre-v101 databases both run this fn, and
the parity test pins the two shapes equal. Idempotent on replay.

State machine mirrors run_batches: ``pending`` (accepted / between ticks) →
``running`` (the in-tick claim state; non-final advances return to
``pending``) ⇄ ``paused`` (reserved for a future control plane; the feeder
skips) ; terminal: ``completed`` (cursor exhausted) | ``failed``
(deterministic bookkeeping failure) | ``cancelled`` (reserved).

v103 (M61) rides the same module (the v99/v100 shared-module precedent):
``job_batch_operations.archived_at`` — the task-center archive bit for batch
ops, exactly the run_batches v100 semantics (manual archive stamps the
column; auto-archive is a query-time reading, never a sweeper write; the
retention sweep deletes manual archives past 90 days and auto-archived rows
past finished_at + 7d + 90d). The partial index serves the archived view's
keyset scan and the retention sweep's cutoff probe. Same guarded-ALTER home
rule, idempotent on replay.
"""

from __future__ import annotations

from typing import Any

_JOB_BATCH_OPERATIONS_DDL = """
create table if not exists job_batch_operations (
  id text primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  kind text not null check(kind in (
    'rerun', 'rerun_by_failure', 'delete', 'run_to',
    'pause', 'resume', 'upgrade_workflow', 'package', 'clear_packed')),
  status text not null default 'pending'
    check(status in ('pending', 'running', 'paused', 'failed', 'completed', 'cancelled')),
  -- Frozen at accept time: normalized job_ids + kind params (node_key /
  -- from_failed_node / mode / target_node_key / reason / operator /
  -- category / strategy / from_node_key). The feeder never re-resolves the
  -- selection against live filters.
  target_spec_json text not null default '{}',
  next_index integer not null default 0,
  items_total integer not null default 0,
  -- Recomputed from the results child table at every cursor advance
  -- (self-healing, the jobs_created=count_jobs_in_run precedent).
  succeeded_count integer not null default 0,
  skipped_count integer not null default 0,
  failed_count integer not null default 0,
  -- Kind-specific terminal output (package: package_filename/download_url).
  output_json text not null default '{}',
  chunk_errors_json text not null default '[]',
  error_message text not null default '',
  created_by text not null default '',
  created_at timestamptz not null default current_timestamp,
  updated_at timestamptz not null default current_timestamp,
  finished_at timestamptz
);
-- One row per job per operation; (op_id, seq) doubles as the replay guard
-- (insert ... on conflict do nothing) and the keyset ordering for the
-- results endpoint. payload_json carries the full sync-shape result dict.
create table if not exists job_batch_operation_results (
  op_id text not null references job_batch_operations(id) on delete cascade,
  seq integer not null,
  job_id text not null,
  status text not null check(status in ('succeeded', 'skipped', 'failed')),
  reason_code text,
  message text,
  payload_json text not null default '{}',
  created_at timestamptz not null default current_timestamp,
  primary key (op_id, seq)
);
-- The feeder's claim scan: pending/running is a small set by construction
-- (idx_run_batches_active precedent).
create index if not exists idx_job_batch_operations_active
  on job_batch_operations(status) where status in ('pending', 'running');
create index if not exists idx_job_batch_operations_workspace
  on job_batch_operations(workspace_id, created_at desc);
"""


def migrate_job_batch_operations(conn: Any) -> None:
    """Create the job_batch_operations tables (v101); idempotent on replay."""
    conn.execute(_JOB_BATCH_OPERATIONS_DDL)


_JOB_BATCH_OPERATIONS_ARCHIVE_DDL = """
alter table job_batch_operations
  add column if not exists archived_at timestamptz;
create index if not exists idx_job_batch_operations_archived
  on job_batch_operations(archived_at) where archived_at is not null;
"""


def migrate_job_batch_operations_archive(conn: Any) -> None:
    """Add the archive column + partial index (v103, M61); idempotent."""
    conn.execute(_JOB_BATCH_OPERATIONS_ARCHIVE_DDL)
