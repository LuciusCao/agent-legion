"""Schema v95 (#1123): ``studio_chat_messages`` kind check gains ``'draft'``.

The studio-agent save tools (save_workflow_draft / save_node_code_draft)
append a synthetic ``draft`` timeline message on the success path so the
chat draft cards also appear for subagent (Agent/AgentSwarm) saves — kimi
never forwards a subagent's internal tool calls as ACP session/update
notifications, so the ``tool_call`` messages the cards used to derive from
never land for them (see studio_chat/draft_events.py).

Same guarded-ALTER home rule as v87/v90/v94: the DDL lives ONLY here
(postgres_schema.sql sits at its budget ceiling); the create-table check in
the schema file keeps the pre-v95 list and this migration widens it on every
init_db replay. Idempotent on replay: drop-if-exists + add.
"""

from __future__ import annotations

from typing import Any

_DRAFT_KIND_DDL = """
alter table studio_chat_messages drop constraint if exists studio_chat_messages_kind_check;
alter table studio_chat_messages add constraint studio_chat_messages_kind_check
  check(kind in ('text', 'tool_call', 'plan', 'permission', 'status', 'thought', 'draft'))
"""


def migrate_studio_chat_message_draft_kind(conn: Any) -> None:
    """Widen the message kind check with 'draft' (v95)."""
    conn.execute(_DRAFT_KIND_DDL)
