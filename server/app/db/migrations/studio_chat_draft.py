"""Schema v57: studio chat canvas draft mirror.

- ``studio_chat_sessions.draft_yaml`` (nullable): the canvas' unpublished
  workflow draft YAML, pushed by the frontend through the PUT context route
  so the session's agent can read it through the get_studio_context MCP tool
  (same delivery channel as v45's selected_node_key).
Idempotent on replay: the statement uses IF NOT EXISTS.
"""

from __future__ import annotations

from typing import Any

_STUDIO_CHAT_DRAFT_DDL = """
alter table studio_chat_sessions add column if not exists draft_yaml text
"""


def migrate_studio_chat_draft(conn: Any) -> None:
    """Apply the v57 studio chat draft column (idempotent)."""
    conn.execute(_STUDIO_CHAT_DRAFT_DDL)
