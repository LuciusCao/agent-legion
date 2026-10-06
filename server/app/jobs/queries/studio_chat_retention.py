"""Studio chat session retention purge (#1041).

The SQL half of ``studio_chat/retention.py``: the only consumer that reads
the soft-delete (#872, v89) / archive (#924, v90) visibility stamps as a
clock instead of a flag. Split from studio_chat_visibility.py (file budget);
composed into ``StudioChatQueriesMixin`` so the JobQueries surface carries it.
"""

from __future__ import annotations

from datetime import datetime

from server.app.jobs.queries.connection import ConnectionQueriesMixin

# Retention candidate: only a closed/error row (no live runtime state) whose
# effective visibility stamp is older than the cutoff. The %s is the cutoff;
# a row with neither stamp yields NULL and never matches.
_EXPIRED_PREDICATE = "status in ('closed', 'error') and coalesce(deleted_at, archived_at) < %s"


class StudioChatRetentionQueriesMixin(ConnectionQueriesMixin):
    """Keyset page + guarded physical delete of expired chat sessions."""

    def page_expired_studio_chat_sessions(
        self, cutoff: datetime, after_id: str, limit: int
    ) -> list[str]:
        """Retention candidates: closed sessions whose visibility stamp is
        older than ``cutoff``, keyset-paged by id.

        The stamp is ``coalesce(deleted_at, archived_at)``: a session deleted
        from the archive view keeps the full window from its *deletion* (the
        countdown the delete prompt promised), never the older archive stamp.
        Rows still in a live status are never candidates."""
        with self._connect_read() as conn:
            rows = conn.execute(
                "select id from studio_chat_sessions"
                f" where {_EXPIRED_PREDICATE} and id > %s order by id limit %s",
                (cutoff, after_id, limit),
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def purge_expired_studio_chat_sessions(
        self, session_ids: list[str], cutoff: datetime
    ) -> list[str]:
        """Physically delete the given retention candidates.

        One statement, so the row and its whole message history
        (``studio_chat_messages`` cascades on the FK; publish requests keep
        their row with the link nulled) go together or not at all — no
        orphan messages, no dangling session. The candidate predicate is
        re-checked here: a session unarchived (stamp cleared) or resumed
        (status left closed/error) since the page read is not deleted.
        Returns the ids actually removed."""
        if not session_ids:
            return []
        with self.write() as conn:
            rows = conn.execute(
                "delete from studio_chat_sessions"
                f" where id = any(%s) and {_EXPIRED_PREDICATE} returning id",
                (session_ids, cutoff),
            ).fetchall()
        return [str(row["id"]) for row in rows]
