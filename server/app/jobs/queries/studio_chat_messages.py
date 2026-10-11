"""Persistence for studio chat messages (schema v43, phase 3 chunk 4).

Split from studio_chat.py (file budget): session CRUD stays there, message
CRUD lives here; StudioChatQueriesMixin inherits this mixin so the composed
JobQueries surface is unchanged.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from server.app.jobs.queries.studio_chat_message_reads import StudioChatMessageReadQueriesMixin


def _message_record(
    message_id: str, session_id: str, kind: str, role: str, content: dict[str, Any], row: Any
) -> dict[str, Any]:
    return {
        "id": message_id,
        "session_id": session_id,
        "kind": kind,
        "role": role,
        "content": content,
        "seq": row["seq"],
        "created_at": row["created_at"],
    }


class StudioChatMessageQueriesMixin(StudioChatMessageReadQueriesMixin):
    """Writes to studio_chat_messages (reads inherited from the read mixin)."""

    def append_studio_chat_message(
        self, session_id: str, kind: str, role: str, content: dict[str, Any]
    ) -> dict[str, Any]:
        message_id = uuid4().hex
        with self.connect() as conn:
            row = conn.execute(
                "insert into studio_chat_messages(id, session_id, kind, role, content_json)"
                " values (%s, %s, %s, %s, %s) returning seq, created_at",
                (message_id, session_id, kind, role, json.dumps(content)),
            ).fetchone()
        assert row is not None  # insert ... returning always yields a row
        return _message_record(message_id, session_id, kind, role, content, row)

    def append_studio_chat_message_if_live(
        self,
        session_id: str,
        kind: str,
        role: str,
        content: dict[str, Any],
        live_statuses: tuple[str, ...],
        error_detail_is: str | None = None,
    ) -> dict[str, Any] | None:
        """Insert only while the session is live and not soft-deleted (#915).

        One statement: the liveness predicate and the INSERT cannot be split
        by a concurrent close / soft delete. ``FOR SHARE`` on the session row
        makes a racing status/deleted_at UPDATE wait for this insert (or, if
        it committed first, re-evaluates the predicate on the new row
        version), so a dead session never gets the row. ``error_detail_is``
        additionally pins the row's current ``error_detail`` inside the same
        predicate (#1095: an orphan stamp's event never lands once another
        generation rewrote the row with its own failure). None = not inserted.
        """
        message_id = uuid4().hex
        with self.connect() as conn:
            row = conn.execute(
                "with live as (select id from studio_chat_sessions where id=%s and status = any(%s)"
                " and deleted_at is null and error_detail = coalesce(%s, error_detail) for share)"
                " insert into studio_chat_messages(id, session_id, kind, role, content_json)"
                " select %s, live.id, %s, %s, %s from live returning seq, created_at",
                (session_id, list(live_statuses), error_detail_is, message_id, kind, role)
                + (json.dumps(content),),
            ).fetchone()
        if row is None:
            return None
        return _message_record(message_id, session_id, kind, role, content, row)

    def update_studio_chat_message_content(self, message_id: str, content: dict[str, Any]) -> None:
        """Replace a message's content (streaming agent text coalescing)."""
        with self.connect() as conn:
            conn.execute(
                "update studio_chat_messages set content_json=%s where id=%s",
                (json.dumps(content), message_id),
            )
