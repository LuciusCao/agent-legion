"""Persistence for studio chat messages (schema v43, phase 3 chunk 4).

Split from studio_chat.py (file budget): session CRUD stays there, message
CRUD lives here; StudioChatQueriesMixin inherits this mixin so the composed
JobQueries surface is unchanged.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class StudioChatMessageQueriesMixin(ConnectionQueriesMixin):
    """CRUD for studio_chat_messages."""

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
        return {
            "id": message_id,
            "session_id": session_id,
            "kind": kind,
            "role": role,
            "content": content,
            "seq": row["seq"],
            "created_at": row["created_at"],
        }

    def update_studio_chat_message_content(self, message_id: str, content: dict[str, Any]) -> None:
        """Replace a message's content (streaming agent text coalescing)."""
        with self.connect() as conn:
            conn.execute(
                "update studio_chat_messages set content_json=%s where id=%s",
                (json.dumps(content), message_id),
            )

    def list_studio_chat_messages(
        self, session_id: str, *, after_seq: int = 0, limit: int = 500
    ) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = conn.execute(
                "select id, session_id, kind, role, content_json, seq, created_at"
                " from studio_chat_messages where session_id=%s and seq>%s"
                " order by seq limit %s",
                (session_id, after_seq, limit),
            ).fetchall()
        messages = []
        for row in rows:
            record = dict(row)
            record["content"] = json.loads(record.pop("content_json") or "{}")
            messages.append(record)
        return messages

    def count_studio_chat_user_messages(self, session_id: str) -> int:
        with self._connect_read() as conn:
            row = conn.execute(
                "select count(*) as n from studio_chat_messages"
                " where session_id=%s and kind='text' and role='user'",
                (session_id,),
            ).fetchone()
        assert row is not None  # count(*) always yields a row
        return int(row["n"])
