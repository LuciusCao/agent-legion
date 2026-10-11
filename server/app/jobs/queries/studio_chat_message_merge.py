"""Shallow-merge content update for coalesced studio chat rows (#1120).

Split from studio_chat_messages.py (file budget): tool_call coalescing needs
a read-modify-write UPDATE, unlike ``update_studio_chat_message_content``'s
whole-content replace (streaming text coalescing). Sits between
studio_chat_messages and studio_chat_transcript in the mixin chain so the
composed JobQueries surface gains it unchanged.
"""

from __future__ import annotations

import json
from typing import Any

from server.app.jobs.queries.studio_chat_messages import StudioChatMessageQueriesMixin


class StudioChatMessageMergeQueriesMixin(StudioChatMessageQueriesMixin):
    """Merge-style content update for studio_chat_messages."""

    def merge_studio_chat_message_content(
        self, message_id: str, patch: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Shallow-merge ``patch`` over the row's content (``{**old, **new}``).

        A tool_call_update frame carries only the changed fields (status /
        rawOutput); merging keeps the first frame's title/kind/rawInput.
        Returns the merged content (the SSE update frame's payload), or None
        when the row is gone. The ``FOR UPDATE`` read and the write share one
        transaction; same-session writers already serialize on the session
        runtime lock (studio_chat/tool_call_coalesce.py).
        """
        with self.connect() as conn:
            row = conn.execute(
                "select content_json from studio_chat_messages where id=%s for update",
                (message_id,),
            ).fetchone()
            if row is None:
                return None
            merged = {**json.loads(row["content_json"] or "{}"), **patch}
            conn.execute(
                "update studio_chat_messages set content_json=%s where id=%s",
                (json.dumps(merged), message_id),
            )
        return merged
