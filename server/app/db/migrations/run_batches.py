"""Schema v99 (#1117): the run_batches table — runs 异步模式的 batch 状态行。

Infrastructure for the async runs mode (docs/architecture/runs-async-mode.md):
one row per asynchronously-fed run, holding the frozen target spec (items +
node config + revision), the item cursor, the counters, and the feeder state
machine. This is NOT the retired Campaign product entity (#505/#532, PR #541
never reached main): no modes, no watermark column, no list/register API —
the row's lifecycle follows its run (1:1, cascade delete).

The DDL rides the apply fn (postgres_schema.sql sits at its raw-line ceiling;
v76/v84/v96 precedent): fresh and pre-v99 databases both run this fn, and the
parity test pins the two shapes equal. Idempotent on replay.

State machine: ``pending`` (accepted/reset/between ticks, awaiting claim) →
``running`` (the in-tick claim state; a claimed batch returns to ``pending``
on every non-final advance so the next tick reclaims it immediately — the
intake consumer's queued-between-chunks precedent) ⇄ ``paused`` (operator
intent; feeder skips); terminal: ``completed`` (cursor exhausted) |
``failed`` (deterministic failure — vanished revision, corrupt target spec;
transient errors back off instead) | ``cancelled`` (any non-terminal state).
"""

from __future__ import annotations

from typing import Any

_RUN_BATCHES_DDL = """
create table if not exists run_batches (
  run_id text primary key references runs(id) on delete cascade,
  workspace_id text not null references workspaces(id) on delete cascade,
  status text not null default 'pending'
    check(status in ('pending', 'running', 'paused', 'failed', 'completed', 'cancelled')),
  -- Frozen at accept time: items (text items verbatim), workflow_revision_id,
  -- node_config, node_code_versions, text_default_filename (RUN-FREEZE-001).
  target_spec_json text not null default '{}',
  next_index integer not null default 0,
  items_total integer not null default 0,
  -- Recomputed as count_jobs_in_run at every cursor advance (self-healing —
  -- a crash mid-chunk is absorbed by the dedup probe on replay).
  jobs_created integer not null default 0,
  chunk_errors_json text not null default '[]',
  error_message text not null default '',
  created_by text not null default '',
  created_at timestamptz not null default current_timestamp,
  updated_at timestamptz not null default current_timestamp,
  finished_at timestamptz
);
-- The feeder's claim scan: pending/running is a small set by construction,
-- so the partial index keeps the per-tick query cheap regardless of how many
-- terminal rows accumulate (PR #541's idx_campaigns_active precedent).
create index if not exists idx_run_batches_active on run_batches(status)
  where status in ('pending', 'running');
create index if not exists idx_run_batches_workspace
  on run_batches(workspace_id, created_at desc);
"""


def migrate_run_batches(conn: Any) -> None:
    """Create the run_batches table (v99); idempotent on replay."""
    conn.execute(_RUN_BATCHES_DDL)
