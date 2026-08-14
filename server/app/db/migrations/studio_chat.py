"""Schema v43: studio chat sessions/messages tables (ACP conversation backend)."""

from __future__ import annotations

from typing import Any

# Studio chat (schema v43, phase 3 chunk 4): one row per Studio conversation
# session (backed by an ACP agent subprocess, in-process only for v1) plus the
# persisted message timeline. capability_snapshot_json freezes the agent
# capabilities negotiated at initialize; mcp_status records the behavioural
# smoke signal for agent-legion MCP tool visibility (unknown -> verified |
# unverified). Idempotent on replay: all statements use IF NOT EXISTS.
_STUDIO_CHAT_DDL = """
create table if not exists studio_chat_sessions (
  id text primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  user_id text not null references users(id) on delete cascade,
  agent_id text not null,
  title text not null default '',
  status text not null default 'starting'
    check(status in ('starting', 'idle', 'running', 'awaiting_permission',
                     'closed', 'error')),
  acp_session_id text,
  capability_snapshot_json text not null default '{}',
  allow_all_permissions boolean not null default false,
  mcp_status text not null default 'unknown'
    check(mcp_status in ('unknown', 'verified', 'unverified')),
  error_detail text not null default '',
  created_at timestamptz not null default current_timestamp,
  updated_at timestamptz not null default current_timestamp,
  closed_at timestamptz
);
create index if not exists idx_studio_chat_sessions_workspace
  on studio_chat_sessions(workspace_id, created_at desc);

create table if not exists studio_chat_messages (
  id text primary key,
  seq bigint generated always as identity,
  session_id text not null references studio_chat_sessions(id) on delete cascade,
  kind text not null
    check(kind in ('text', 'tool_call', 'plan', 'permission', 'status')),
  role text not null check(role in ('user', 'agent', 'system')),
  content_json text not null default '{}',
  created_at timestamptz not null default current_timestamp
);
create unique index if not exists idx_studio_chat_messages_seq
  on studio_chat_messages(seq);
create index if not exists idx_studio_chat_messages_session
  on studio_chat_messages(session_id, seq)
"""


def migrate_studio_chat_tables(conn: Any) -> None:
    """Create the studio chat tables (v43); idempotent on replay."""
    conn.execute(_STUDIO_CHAT_DDL)
