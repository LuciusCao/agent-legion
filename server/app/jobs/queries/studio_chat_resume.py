"""Resume claim for studio chat sessions (closed/error -> starting).

Split from studio_chat.py (file budget). The resume claim shares the session
creation cap's advisory lock (_CAP_LOCK_KEY, defined here and imported by
studio_chat.py) so a resume spawning a fresh agent subprocess cannot race
concurrent creators past the active-session cap.
"""

from __future__ import annotations

from server.app.jobs.queries.studio_chat_messages import StudioChatMessageQueriesMixin

# Advisory-lock key serializing session creation AND resume against the
# active cap (studio_chat.py imports it for the creation path).
_CAP_LOCK_KEY = "studio_chat_session_cap"


class StudioChatResumeQueriesMixin(StudioChatMessageQueriesMixin):
    """Atomic closed/error -> starting resume claim for studio_chat_sessions."""

    def claim_studio_chat_resume(self, session_id: str, *, max_active: int) -> bool:
        """Move closed/error -> starting under the cap advisory lock.

        The active-count predicate rides the same UPDATE (one statement, one
        transaction, the creation cap's advisory lock held), so concurrent
        creators and resumers cannot all observe a below-cap count and each
        spawn a subprocess. False means the session left a resumable state OR
        the cap is hit; the caller re-reads the row to tell them apart.
        """
        with self.connect() as conn:
            conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (_CAP_LOCK_KEY,))
            row = conn.execute(
                "update studio_chat_sessions set status='starting', error_detail='',"
                " closed_at=null, updated_at=current_timestamp"
                " where id=%s and status in ('closed', 'error')"
                " and (select count(*) from studio_chat_sessions"
                " where status in ('starting', 'idle', 'running', 'awaiting_permission'))"
                " < %s returning id",
                (session_id, max_active),
            ).fetchone()
        return row is not None
