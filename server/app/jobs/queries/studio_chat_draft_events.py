"""Guarded INSERT for synthetic draft_saved timeline events (#1123, codex P2-b/c).

Split from studio_chat_messages.py (file budget): the synthetic draft card
event (studio_chat/draft_events.py) needs a stricter gate than the generic
``append_studio_chat_message_if_live`` — one statement that atomically
asserts, beyond session liveness:

- the session belongs to the saving route's workspace (the MCP session header
  is an attribution hint, not an authority);
- ``archived_at is null`` — archive stamps the row BEFORE the close lands
  (session_archive.py), so the stamp alone must already fence the write;
- draft currency: the event announces the CURRENT draft. A stalled writer
  (concurrent subagent saves of the same entity) must not append its event
  after the save that overwrote it — the landed event order then matches the
  authoritative draft-write order, and the frontend's last-seq-wins merge
  picks the actually-current hash.

The caller additionally pins the runtime generation under the service's
runtime-registry lock (draft_events.py, #1123 codex P2-a); this method is the
DB-side half of that atomicity story.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from server.app.jobs.queries.studio_chat_messages import (
    StudioChatMessageQueriesMixin,
    _message_record,
)

DRAFT_EVENT_KIND = "draft"

_SESSION_GATE = (
    "with live as (select id from studio_chat_sessions"
    " where id=%s and workspace_id=%s and status = any(%s)"
    " and deleted_at is null and archived_at is null for share)"
)
_INSERT = (
    " insert into studio_chat_messages(id, session_id, kind, role, content_json)"
    " select %s, live.id, 'draft', 'agent', %s from live, current_draft"
    " returning seq, created_at"
)
# Currency gates (P2-b): the announced draft must still be the current one.
# node_code: the draft row (save_draft overwrites it in place) is still a
# draft with this definition_hash. workflow: the single-row draft's
# updated_at is still the one this CAS save produced.
_NODE_CODE_GATE = (
    ", current_draft as (select 1 from versioned_entities"
    " where id=%s and status='draft' and definition_hash=%s)"
)
_WORKFLOW_GATE = (
    ", current_draft as (select 1 from workspace_workflow_drafts"
    " where workspace_id=%s and updated_at=%s)"
)


class StudioChatDraftEventQueriesMixin(StudioChatMessageQueriesMixin):
    """Currency-guarded draft-event insert for studio_chat_messages."""

    def append_studio_chat_node_code_draft_event(
        self,
        session_id: str,
        workspace_id: str,
        live_statuses: tuple[str, ...],
        content: dict[str, Any],
        *,
        draft_row_id: str,
        code_hash: str,
    ) -> dict[str, Any] | None:
        """Insert the node_code draft_saved event iff the session gate and the
        draft-currency gate both hold (None = gated out)."""
        message_id = uuid4().hex
        with self.connect() as conn:
            row = conn.execute(
                _SESSION_GATE + _NODE_CODE_GATE + _INSERT,
                (
                    session_id,
                    workspace_id,
                    list(live_statuses),
                    draft_row_id,
                    code_hash,
                    message_id,
                    json.dumps(content),
                ),
            ).fetchone()
        if row is None:
            return None
        return _message_record(message_id, session_id, DRAFT_EVENT_KIND, "agent", content, row)

    def append_studio_chat_workflow_draft_event(
        self,
        session_id: str,
        workspace_id: str,
        live_statuses: tuple[str, ...],
        content: dict[str, Any],
        *,
        draft_updated_at: Any,
    ) -> dict[str, Any] | None:
        """Insert the workflow draft_saved event iff the session gate holds and
        the draft row still carries this save's updated_at (None = gated out)."""
        message_id = uuid4().hex
        with self.connect() as conn:
            row = conn.execute(
                _SESSION_GATE + _WORKFLOW_GATE + _INSERT,
                (
                    session_id,
                    workspace_id,
                    list(live_statuses),
                    workspace_id,
                    draft_updated_at,
                    message_id,
                    json.dumps(content),
                ),
            ).fetchone()
        if row is None:
            return None
        return _message_record(message_id, session_id, DRAFT_EVENT_KIND, "agent", content, row)
