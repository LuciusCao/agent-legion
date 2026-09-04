"""Schema v76 (#434 renumber; born v75 until #427's node_runs_skill_key
claimed 75 on develop): agent-initiated workflow publish requests (#416).

``studio_publish_requests`` holds the agent→human publish handshake. The
agent's ``request_workflow_publish`` MCP tool INSERTs a pending row; the row
only becomes ``confirmed`` when a human calls the confirm endpoint (full user
session — ``reject_studio_agent_scope`` guards the route, STUDIO-AGENT-001),
which replays the same ``publish_workflow_draft`` gates the Studio button
uses. The agent never publishes directly: the tool creates no revision.

This module owns the table's DDL — postgres_schema.sql does not create it
(the schema file sits at its absolute line ceiling; fresh and pre-v76
databases both run this apply fn — the parity test pins the shapes equal).

State machine (single row per workspace while pending):
``pending`` → ``confirming`` (the human confirm's claim; cancel/new agent
requests cannot touch it, #429 三轮 P1) → ``confirmed`` (publish succeeded;
result_revision_id records the new revision, NULL for a runtime-only
in-place save) | back to ``pending`` (the claimed publish was refused —
fixable, retryable) | ``rejected`` (human cancelled);
``pending`` → ``superseded`` (a newer agent request or a manual publish
displaced it) | ``expired`` (past ``expires_at``, lazily swept on read).

Two #429 三轮 P1 hardening pieces live in the DDL itself:

- The partial unique index on ``(workspace_id) where status='pending'``:
  at most one pending row per workspace is now a DATABASE invariant, not a
  read-modify-write courtesy. Two concurrent tool calls both doing
  "supersede-matching-zero-rows then INSERT" land one INSERT on the index
  violation; the loser retries (supersede now hits the winner's row).
  ``confirming`` rows are deliberately NOT in the index — the claim is an
  UPDATE, and indexing it would break the pending→confirming→pending
  retry loop; the "no new pending while confirming" rule is enforced by
  the create transaction's guard (#429 三轮 P1-2, in-transaction since
  四轮 codex P1).
- ``draft_hash``: the sha256 of the server draft YAML at request time.
  The confirm replays it against the current server draft and refuses
  with 409 on a mismatch — the human confirms exactly the draft the agent
  asked about, never a newer one that silently appeared (#429 三轮 P1-3).
- ``claimed_at`` (#429 四轮 P1): stamped on claim, the TTL clock for a
  process that died between claim and resolve — a confirming row older
  than CONFIRMING_STALE_SECONDS (5 min; a healthy publish takes seconds)
  is swept to ``expired`` (the create transaction does it in-line under
  the claim-shared advisory lock). See
  studio_publish_request_support.CONFIRMING_STALE_SECONDS.
"""

from __future__ import annotations

from typing import Any

_STUDIO_PUBLISH_REQUESTS_DDL = """
create table if not exists studio_publish_requests (
  id text primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  chat_session_id text references studio_chat_sessions(id) on delete set null,
  status text not null default 'pending'
    check(status in ('pending', 'confirming', 'superseded', 'confirmed', 'rejected', 'expired')),
  created_by text not null default '',
  result_revision_id text,
  draft_hash text,
  created_at timestamptz not null default current_timestamp,
  expires_at timestamptz not null,
  resolved_at timestamptz,
  claimed_at timestamptz
);
create index if not exists idx_studio_publish_requests_workspace_status
  on studio_publish_requests(workspace_id, status, created_at desc);
-- #429 三轮 P1-1: exactly one pending row per workspace, enforced by the
-- index itself. The concurrent-create loser sees the unique violation and
-- retries (its supersede UPDATE then matches the winner's row).
create unique index if not exists idx_studio_publish_requests_pending_workspace
  on studio_publish_requests(workspace_id) where status = 'pending'
"""


def migrate_studio_publish_requests(conn: Any) -> None:
    """Create the studio publish requests table (v76); idempotent on replay."""
    conn.execute(_STUDIO_PUBLISH_REQUESTS_DDL)
