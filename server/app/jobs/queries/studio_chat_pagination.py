"""Cursor-paged history read for the studio chat messages endpoint (#1120 PR-3).

Split from studio_chat_messages.py (file budget): the plain list read there
serves internal consumers, while the HTTP surface needs an explicit
older-side cursor (``has_more``) so the panel can page upwards on scroll.
Composes into the JobQueries chain via studio_chat_resume.py.
"""

from __future__ import annotations

import json
from typing import Any

from server.app.jobs.queries.studio_chat_transcript import StudioChatTranscriptQueriesMixin

# Page sizes for the messages endpoint (server-fixed, not client-tunable,
# #1120 PR-3). The default/after_seq refill path keeps the historical 500-row
# window so the first screen is unchanged until the frontend's page-up lands;
# the before_seq page-up path uses 100: persisted rows are heavy (tool-call
# outputs land inline), and paging up is an explicit user wait, so 100 rows
# already cover a screenful per fetch.
STUDIO_CHAT_PAGE_SIZE = 500
STUDIO_CHAT_PAGE_UP_SIZE = 100


class StudioChatPaginationQueriesMixin(StudioChatTranscriptQueriesMixin):
    """Paged read with an explicit older-side cursor for the messages route."""

    def list_studio_chat_messages_page(
        self,
        session_id: str,
        *,
        after_seq: int = 0,
        before_seq: int | None = None,
        limit: int = STUDIO_CHAT_PAGE_SIZE,
    ) -> tuple[list[dict[str, Any]], bool]:
        """One page of messages, ascending, plus ``has_more`` for paging up.

        ``after_seq`` (exclusive lower bound, forward refill) and
        ``before_seq`` (exclusive upper bound, paging up to older history)
        frame the same seq scan from opposite directions and are mutually
        exclusive — the route rejects a request carrying both (422), so a
        caller reaching here with both set is a programming error. The window
        keeps the LATEST matching rows (desc + reverse, the #411 cap
        semantics of ``list_studio_chat_messages``): with ``before_seq``
        that is exactly the page immediately older than the cursor.

        ``has_more`` reports whether the scanned range holds rows OLDER than
        the returned window. For a ``before_seq`` page-up (and the initial
        load, which scans from seq 0) that is exactly "an earlier page
        exists": one more ``before_seq`` call at this page's oldest seq
        returns a non-empty page. For an ``after_seq`` refill the range is
        bounded below by the client's cursor, so has_more=true signals the
        refill window did not reach back to that cursor (a gap). The value
        comes from a limit+1 probe: "returned count == limit" cannot tell a
        full page from the last one, and an empty page must stay
        distinguishable from "no earlier history".
        """
        if before_seq is not None and after_seq:
            raise ValueError("after_seq and before_seq are mutually exclusive")
        clauses = "session_id=%s and seq>%s"
        params: list[Any] = [session_id, after_seq]
        if before_seq is not None:
            clauses += " and seq<%s"
            params.append(before_seq)
        params.append(limit + 1)
        with self._connect_read() as conn:
            rows = conn.execute(
                "select id, session_id, kind, role, content_json, seq, created_at"
                f" from studio_chat_messages where {clauses}"
                " order by seq desc limit %s",
                params,
            ).fetchall()
        has_more = len(rows) > limit
        messages = []
        for row in reversed(rows[:limit]):
            record = dict(row)
            record["content"] = json.loads(record.pop("content_json") or "{}")
            messages.append(record)
        return messages, has_more
