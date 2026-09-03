"""Persistence for agent-initiated workflow publish requests (schema v75).

One ``pending`` row per workspace at a time: ``create_pending_request``
supersedes (not rejects) an older pending row inside the same transaction as
the INSERT, so the "new request displaces the old" policy is atomic against
concurrent tool calls. Expiry is lazy — every read sweeps rows past their
``expires_at`` to ``expired`` before returning (run-token semantics; no
background timer to keep alive).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from server.app.jobs.queries.connection import ConnectionQueriesMixin

# 10 minutes, mirroring the studio chat permission timeout's "human is
# looking at the screen" scale: long enough to read the compare summary and
# decide, short enough that an abandoned tab cannot pin the dialog open.
PUBLISH_REQUEST_TTL_SECONDS = 600

_REQUEST_COLUMNS = (
    "id, workspace_id, chat_session_id, status, created_by,"
    " result_revision_id, created_at, expires_at, resolved_at"
)


def publish_request_expiry(now: datetime | None = None) -> datetime:
    """The expires_at stamp for a request created at ``now`` (UTC)."""
    return (now or datetime.now(UTC)) + timedelta(seconds=PUBLISH_REQUEST_TTL_SECONDS)


class StudioPublishRequestQueriesMixin(ConnectionQueriesMixin):
    """CRUD for studio_publish_requests (the agent→human publish handshake)."""

    def create_pending_publish_request(
        self,
        workspace_id: str,
        created_by: str,
        chat_session_id: str | None = None,
        *,
        ttl_seconds: int = PUBLISH_REQUEST_TTL_SECONDS,
    ) -> dict[str, Any]:
        """Supersede any pending request and insert a fresh pending row.

        The supersede and the INSERT share one transaction, so two concurrent
        tool calls cannot both observe "no pending row" and leave two behind.
        """
        request_id = uuid4().hex
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        with self.connect() as conn:
            conn.execute(
                "update studio_publish_requests set status='superseded',"
                " resolved_at=current_timestamp"
                " where workspace_id=%s and status='pending'",
                (workspace_id,),
            )
            row = conn.execute(
                "insert into studio_publish_requests"
                "(id, workspace_id, chat_session_id, created_by, expires_at)"
                " values (%s, %s, %s, %s, %s) returning " + _REQUEST_COLUMNS,
                (request_id, workspace_id, chat_session_id, created_by, expires_at),
            ).fetchone()
        if row is None:
            raise RuntimeError("publish request insert did not return a row")
        return dict(row)

    def _sweep_expired_publish_requests(self, workspace_id: str) -> int:
        """Lazily expire overdue pending rows; returns the swept count."""
        with self.connect() as conn:
            cursor = conn.execute(
                "update studio_publish_requests set status='expired',"
                " resolved_at=current_timestamp"
                " where workspace_id=%s and status='pending'"
                " and expires_at < current_timestamp",
                (workspace_id,),
            )
            return cursor.rowcount

    def get_pending_publish_request(self, workspace_id: str) -> dict[str, Any] | None:
        """The workspace's live pending request, None when there is none.

        Expired rows are swept first (lazy expiry): a caller never sees a
        pending row that is past its expires_at.
        """
        self._sweep_expired_publish_requests(workspace_id)
        with self._connect_read() as conn:
            row = conn.execute(
                f"select {_REQUEST_COLUMNS} from studio_publish_requests"
                " where workspace_id=%s and status='pending'"
                " order by created_at desc limit 1",
                (workspace_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_publish_request(self, request_id: str) -> dict[str, Any] | None:
        """One request by id (any status); a pending row past expiry is
        lazily flipped to ``expired`` by the read itself (the UPDATE's
        expires_at predicate rides the statement, so no Python-side datetime
        parsing of the driver's serialized value)."""
        with self.connect() as conn:
            row = conn.execute(
                "update studio_publish_requests set status='expired',"
                " resolved_at=current_timestamp"
                " where id=%s and status='pending' and expires_at < current_timestamp"
                " returning " + _REQUEST_COLUMNS,
                (request_id,),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    f"select {_REQUEST_COLUMNS} from studio_publish_requests where id=%s",
                    (request_id,),
                ).fetchone()
        return dict(row) if row is not None else None

    def resolve_publish_request(
        self,
        request_id: str,
        *,
        status: str,
        result_revision_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Atomically move a pending request to a terminal state; None when
        the request is missing or no longer pending (resolved/expired/swept
        by a racing resolution).

        The status predicate rides the same UPDATE as the write, so two
        concurrent confirm/cancel calls cannot both win: the loser sees no
        row and gets None instead of double-resolving.
        """
        if status not in ("confirmed", "rejected"):
            raise ValueError(f"unsupported terminal status: {status}")
        with self.connect() as conn:
            row = conn.execute(
                "update studio_publish_requests set status=%s,"
                " result_revision_id=%s, resolved_at=current_timestamp"
                " where id=%s and status='pending'"
                " and expires_at > current_timestamp"
                " returning " + _REQUEST_COLUMNS,
                (status, result_revision_id, request_id),
            ).fetchone()
        return dict(row) if row is not None else None
