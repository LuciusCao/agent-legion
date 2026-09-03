"""Schema v76: agent-initiated workflow publish requests (issue #416).

Born as this branch's v75, renumbered to 76 when #427's node_runs_skill_key
claimed v75 on develop (#434).

``studio_publish_requests`` holds the agent→human publish handshake. The
agent's ``request_workflow_publish`` MCP tool INSERTs a pending row; the row
only becomes ``confirmed`` when a human calls the confirm endpoint (full user
session — ``reject_studio_agent_scope`` guards the route, STUDIO-AGENT-001),
which replays the same ``publish_workflow_draft`` gates the Studio button
uses. The agent never publishes directly: the tool creates no revision.

This module owns the table's DDL — postgres_schema.sql does not create it
(the schema file sits at its absolute line ceiling; fresh databases run
every migration after the file replay, and pre-v76 databases run this
apply fn on upgrade, so both paths are covered; the parity test pins
fresh == upgraded).

State machine (single row per workspace while pending):
``pending`` → ``superseded`` (a newer agent request displaced it),
           → ``confirmed`` (human confirmed; result_revision_id records the
             revision the publish produced, or NULL for runtime-only saves),
           → ``rejected`` (human cancelled in the review dialog),
           → ``expired`` (older than ``expires_at``; the sweep happens
             lazily on the next read/write, no background timer).
"""

from __future__ import annotations

from typing import Any

_STUDIO_PUBLISH_REQUESTS_DDL = """
create table if not exists studio_publish_requests (
  id text primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  chat_session_id text references studio_chat_sessions(id) on delete set null,
  status text not null default 'pending'
    check(status in ('pending', 'superseded', 'confirmed', 'rejected', 'expired')),
  created_by text not null default '',
  result_revision_id text,
  created_at timestamptz not null default current_timestamp,
  expires_at timestamptz not null,
  resolved_at timestamptz
);
create index if not exists idx_studio_publish_requests_workspace_status
  on studio_publish_requests(workspace_id, status, created_at desc)
"""


def migrate_studio_publish_requests(conn: Any) -> None:
    """Create the studio publish requests table (v76); idempotent on replay."""
    conn.execute(_STUDIO_PUBLISH_REQUESTS_DDL)
