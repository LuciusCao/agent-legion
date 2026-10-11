"""Read side of studio chat message persistence.

Split from studio_chat_messages.py (file budget, #1095): the write side
(append / live-guarded append / content update) stays there and inherits
this mixin, so the composed JobQueries surface is unchanged.
"""

from __future__ import annotations

import json
from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class StudioChatMessageReadQueriesMixin(ConnectionQueriesMixin):
    """Reads over studio_chat_messages."""

    def list_studio_chat_messages(
        self, session_id: str, *, after_seq: int = 0, limit: int = 500
    ) -> list[dict[str, Any]]:
        """List messages after ``after_seq``, newest first capped at ``limit``.

        The cap takes the LATEST rows, not the earliest (#411): a session
        that outgrew the limit used to hand back its first 500 messages
        while silently dropping everything newer — the UI re-entering such a
        long conversation showed ancient history and the ongoing turn looked
        like it had vanished. desc + reverse keeps the ascending contract of
        the return value; incremental after_seq refills stay exact. Trade-
        off: a client >limit behind refills the newest window and keeps a
        gap (no before_seq API); remounting replaces the list wholesale.
        """
        with self._connect_read() as conn:
            rows = conn.execute(
                "select id, session_id, kind, role, content_json, seq, created_at"
                " from studio_chat_messages where session_id=%s and seq>%s"
                " order by seq desc limit %s",
                (session_id, after_seq, limit),
            ).fetchall()
        messages = []
        for row in reversed(rows):
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
