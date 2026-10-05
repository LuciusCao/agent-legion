"""Visibility stamps on studio_chat_sessions: soft delete (#872, v89) and
archive (#924, v90). Split from studio_chat.py (file budget); both are
conditional single-statement UPDATEs so concurrent callers get exactly one
winner, and both are orthogonal to the status state machine."""

from __future__ import annotations

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class StudioChatVisibilityQueriesMixin(ConnectionQueriesMixin):
    """deleted_at / archived_at stamps."""

    def mark_studio_chat_session_deleted(self, session_id: str) -> bool:
        """Soft-delete stamp (#872, v89): conditional on the row not being
        stamped yet, so two concurrent deletes have exactly one winner.
        Returns whether this call stamped it."""
        with self.connect() as conn:
            row = conn.execute(
                "update studio_chat_sessions set deleted_at=current_timestamp,"
                " updated_at=current_timestamp where id=%s and deleted_at is null"
                " returning id",
                (session_id,),
            ).fetchone()
        return row is not None

    def set_studio_chat_session_archived(self, session_id: str, archived: bool) -> bool:
        """Archive stamp / clear (#924, v90), conditional on the flag actually
        flipping and the row not being soft-deleted. Returns whether this call
        changed the row (False = already in that state, or deleted/missing)."""
        with self.connect() as conn:
            row = conn.execute(
                "update studio_chat_sessions set archived_at="
                " case when %s then current_timestamp else null end,"
                " updated_at=current_timestamp where id=%s and deleted_at is null"
                " and (archived_at is null)=%s returning id",
                (archived, session_id, archived),
            ).fetchone()
        return row is not None
