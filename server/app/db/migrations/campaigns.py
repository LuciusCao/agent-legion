"""Schema v80 (#532 / #505): the campaigns table and its run linkage.

Campaigns productize the drip-feed bulk operations (issue #505's CLI
watermark loop, the design doc ``.zcode/plans/campaign-design-532.md``): one
row = one watermark-gated campaign that the PR-B feeder drains in batches.
The table is carried as an apply fn, not a postgres_schema.sql entry — the
schema file sits at its absolute line ceiling (the v76 precedent,
studio_publish_requests); fresh and pre-v80 databases both run this apply fn,
so the parity test pins the two shapes equal.

State machine: ``pending`` (created, not yet picked up — the feeder's first
pickup CAS-flips it to running so "created but untouched" is explicit and
restart-idempotent) → ``running`` ⇄ ``paused`` (pause is an operator intent,
distinct from a paused workspace: the feeder skips paused workspaces but the
campaign keeps its own status); ``running``/``paused`` → ``failed``
(deterministic failures only — contract violations, vanished workflow
revision, corrupt manifest; transient errors back off instead) |
``completed`` (cursor exhausted) | ``cancelled`` (any non-terminal state).

Column shape notes (design §1.2):

- Counters are real columns (queryable, indexable); the cursor and the
  watermark sampling trail live in ``progress_json`` (the runs.stats_json
  precedent).
- ``target_spec_json``: rerun/upgrade modes store a filter or explicit
  job_ids plus the rerun parameters; submit mode stores either inline items
  (bounded by campaigns.manifest_inline_max_bytes) or a manifest object
  reference (``manifest_storage_key`` + ``manifest_item_count``).
- ``watermark`` default 0 is reserved for a future "no gate" mode (upgrade
  pre-reservation); the service layer enforces watermark >= 1 for created
  campaigns, matching the CLI guard semantics.
- ``runs.campaign_id`` links submit-mode runs back to their campaign
  (two-hop join jobs.run_id → runs.campaign_id; the jobs table — the hottest
  write path in the system — deliberately gets no campaign column). The
  partial index ``idx_runs_campaign`` keeps the campaign detail aggregation
  an index probe, not a workspace scan.
"""

from __future__ import annotations

from typing import Any

_CAMPAIGNS_DDL = """
create table if not exists campaigns (
  id text primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  mode text not null check(mode in ('rerun', 'submit', 'upgrade')),
  status text not null default 'pending'
    check(status in ('pending', 'running', 'paused', 'failed', 'completed', 'cancelled')),
  -- Target spec (design §1.3): rerun/upgrade = filter or explicit job_ids;
  -- submit = inline items or a manifest object reference.
  target_spec_json text not null default '{}',
  -- Cursor + watermark sampling trail (design §1.4): mode-specific cursor
  -- plus the last N (level, ts) samples for the UI sparkline.
  progress_json text not null default '{}',
  -- 0 is reserved (no gate); the service layer enforces >= 1 for creation.
  watermark integer not null default 30000 check(watermark >= 0),
  batch_size integer not null default 5000 check(batch_size >= 1),
  batches_submitted integer not null default 0,
  jobs_succeeded integer not null default 0,
  jobs_skipped integer not null default 0,
  jobs_failed integer not null default 0,
  error_message text not null default '',
  created_by text not null default '',
  created_at timestamptz not null default current_timestamp,
  updated_at timestamptz not null default current_timestamp,
  finished_at timestamptz
);
create index if not exists idx_campaigns_workspace on campaigns(workspace_id, created_at desc);
-- The feeder's active scan: pending/running is a small set by construction
-- (max_active_per_workspace), so the partial index keeps the per-tick query
-- point-lookup cheap regardless of how many terminal campaigns accumulate.
create index if not exists idx_campaigns_active on campaigns(status)
  where status in ('pending', 'running');
"""

_RUNS_CAMPAIGN_DDL = """
alter table runs add column if not exists campaign_id text not null default '';
create index if not exists idx_runs_campaign on runs(campaign_id) where campaign_id <> '';
"""


def migrate_campaigns(conn: Any) -> None:
    """Create the campaigns table and the runs.campaign_id linkage (v80).

    Idempotent on replay: the schema-file replay never creates these objects
    (the file's line budget keeps campaigns out entirely), so both the fresh
    path and the upgrade path run this fn exactly once per database.
    """
    conn.execute(_CAMPAIGNS_DDL)
    conn.execute(_RUNS_CAMPAIGN_DDL)
