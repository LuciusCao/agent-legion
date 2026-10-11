"""Resume claim for studio chat sessions (closed/error -> starting).

Split from studio_chat.py (file budget). The resume claim shares the session
creation cap's advisory lock (_CAP_LOCK_KEY, defined here and imported by
studio_chat.py) so a resume spawning a fresh agent subprocess cannot race
concurrent creators past the active-session cap.
"""

from __future__ import annotations

from server.app.jobs.queries.studio_chat_transcript import StudioChatTranscriptQueriesMixin

# Advisory-lock key serializing session creation AND resume against the
# active cap (studio_chat.py imports it for the creation path).
_CAP_LOCK_KEY = "studio_chat_session_cap"

_CLAIM_SQL = (
    "update studio_chat_sessions set status='starting', error_detail='',"
    " closed_at=null, updated_at=current_timestamp"
    " where id=%s and status in ({statuses})"
    " and deleted_at is null and archived_at is null"
    " and (select count(*) from studio_chat_sessions"
    " where status in ('starting', 'idle', 'running', 'awaiting_permission'))"
    " < %s returning id"
)
_CLAIM_FROM_CLOSED_ERROR = _CLAIM_SQL.format(statuses="'closed', 'error'")
# #1238 codex P1 (round 4): the send-path auto-resume claims idle/error ->
# starting in ONE statement — the row never transits an error state that
# drops out of the capacity count while its runtime still lives.
# #1238 adversarial P1-1: ...and the count excludes the claimed row itself —
# an idle row being resumed is already counted, so idle->starting is a
# net-zero migration and must not trip the cap when the active count equals
# it (nor bump a second concurrent auto-resume out). Error rows are not
# counted, so the self-exclusion is idempotent for them. The closed/error
# (manual resume) variant keeps counting everything: those rows never count.
_CLAIM_FROM_IDLE_ERROR = (
    "update studio_chat_sessions set status='starting', error_detail='',"
    " closed_at=null, updated_at=current_timestamp"
    " where id=%s and status in ('idle', 'error')"
    " and deleted_at is null and archived_at is null"
    " and (select count(*) from studio_chat_sessions"
    " where status in ('starting', 'idle', 'running', 'awaiting_permission')"
    " and id <> %s)"
    " < %s returning id"
)


class StudioChatResumeQueriesMixin(StudioChatTranscriptQueriesMixin):
    """Atomic resume claim for studio_chat_sessions (from-statuses -> starting)."""

    def claim_studio_chat_resume(
        self,
        session_id: str,
        *,
        max_active: int,
        from_statuses: tuple[str, ...] = ("closed", "error"),
    ) -> bool:
        """Move a from-statuses row -> starting under the cap advisory lock.

        The active-count predicate rides the same UPDATE (one statement, one
        transaction, the creation cap's advisory lock held), so concurrent
        creators and resumers cannot all observe a below-cap count and each
        spawn a subprocess; the idle/error variant's count excludes the
        claimed row itself (already counted while idle, adversarial P1-1).
        False means the session left a resumable state OR the cap is hit; the
        caller re-reads the row to tell them apart.
        """
        params: tuple[str | int, ...]
        if from_statuses == ("idle", "error"):
            sql, params = _CLAIM_FROM_IDLE_ERROR, (session_id, session_id, max_active)
        elif from_statuses == ("closed", "error"):
            sql, params = _CLAIM_FROM_CLOSED_ERROR, (session_id, max_active)
        else:
            raise ValueError(f"unknown from_statuses: {from_statuses!r}")
        with self.connect() as conn:
            conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (_CAP_LOCK_KEY,))
            row = conn.execute(sql, params).fetchone()
        return row is not None
