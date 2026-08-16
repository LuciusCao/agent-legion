"""Schema v45: studio chat workspace context binding + thought messages.

- ``auth_scoped_tokens.workspace_id`` (nullable): a run token minted for a
  Studio chat session is bound to that session's workspace; the studio-agent
  tool surface refuses workspace-path endpoints for any other workspace
  (STUDIO-AGENT-001 scoped semantics). Self-service tokens (origin='user')
  stay unbound (NULL) and keep the previous behaviour.
- ``studio_chat_sessions.selected_node_key`` (nullable): the node the human
  currently has selected in Studio, pushed by the frontend so the session's
  agent can read the live value through the get_studio_context MCP tool.
- ``studio_chat_messages`` kind check gains ``'thought'`` so streamed
  agent_thought_chunk updates persist as collapsible timeline entries.
Idempotent on replay: all statements use IF [NOT] EXISTS.
"""

from __future__ import annotations

from typing import Any

_STUDIO_CHAT_CONTEXT_DDL = """
alter table auth_scoped_tokens add column if not exists workspace_id text;
alter table studio_chat_sessions add column if not exists selected_node_key text;
alter table studio_chat_messages drop constraint if exists studio_chat_messages_kind_check;
alter table studio_chat_messages add constraint studio_chat_messages_kind_check
  check(kind in ('text', 'tool_call', 'plan', 'permission', 'status', 'thought'))
"""


def migrate_studio_chat_context(conn: Any) -> None:
    """Apply the v45 studio chat context columns and thought kind (idempotent)."""
    conn.execute(_STUDIO_CHAT_CONTEXT_DDL)
