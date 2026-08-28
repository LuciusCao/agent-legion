"""Tail read of studio chat messages for the resume transcript rebuild.

Split from studio_chat_messages.py (file budget): the incremental
pagination read (after_seq, oldest-first) serves the chat panel and keeps
its semantics untouched; the resume transcript needs the most recent
messages instead, walked backwards from a seq watermark so a long session
injects recent context rather than the stale opening.
"""

from __future__ import annotations

import json
from typing import Any

from server.app.jobs.queries.studio_chat_messages import StudioChatMessageQueriesMixin


class StudioChatTranscriptQueriesMixin(StudioChatMessageQueriesMixin):
    """Most-recent window read for transcript rebuilds (resume context)."""

    def list_studio_chat_messages_tail(
        self, session_id: str, *, before_seq: int, limit: int = 500
    ) -> list[dict[str, Any]]:
        """Up to ``limit`` messages with seq < ``before_seq``, oldest first.

        ``before_seq`` excludes the message that triggered the rebuild (the
        current user prompt rides along as prompt text) and anything newer.
        """
        with self._connect_read() as conn:
            rows = conn.execute(
                "select id, session_id, kind, role, content_json, seq, created_at"
                " from studio_chat_messages where session_id=%s and seq<%s"
                " order by seq desc limit %s",
                (session_id, before_seq, limit),
            ).fetchall()
        messages = []
        for row in reversed(rows):
            record = dict(row)
            record["content"] = json.loads(record.pop("content_json") or "{}")
            messages.append(record)
        return messages
