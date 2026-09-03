"""Persistence for agent-initiated workflow publish requests (schema v75).

The per-workspace single-pending invariant is a partial unique index
(``workspace_id where status='pending'``, #429 三轮 P1-1), not a
read-modify-write courtesy: ``create_pending_request`` supersede-then-INSERTs
in one transaction, and when a concurrent create already landed its pending
row, the loser's INSERT hits the index and the whole transaction retries —
on retry the supersede UPDATE matches the winner's row, so exactly one
pending row survives and the loser's request takes over the slot (the same
supersede semantics as sequential calls). The manual publish path displaces
pending rows the same way (``supersede_pending_publish_requests``, #429).

``confirming`` (#429 三轮 P1-2) is the claim state: the confirm endpoint
atomically moves pending→confirming before it publishes, so a cancel racing
the publish can no longer land ``rejected`` on a row whose revision is about
to go live. The claim/resolve transitions live in
studio_publish_request_claims.py (split for file budget); this module keeps
create / supersede / lazy expiry / reads.

Expiry is lazy — the pending read is pure (no sweep on the 5s poll path),
and the terminal ``expired`` row is recorded by the write-side
observed-expiry path (``expire_pending_publish_request``; status read by id
sweeps in the same statement it reads with). Resolution NEVER checks
``expires_at``: the claim step already checked TTL, and a resolve whose
underlying publish already landed must record the effect truthfully (a TTL
expiring mid-publish must not deny a revision that exists — TOCTOU, #429).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from psycopg.errors import UniqueViolation

from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.jobs.queries.studio_publish_request_claims import (
    StudioPublishRequestClaimQueriesMixin,
)

# 10 minutes, mirroring the studio chat permission timeout's "human is
# looking at the screen" scale: long enough to read the compare summary and
# decide, short enough that an abandoned tab cannot pin the dialog open.
PUBLISH_REQUEST_TTL_SECONDS = 600

# How many times create_pending_publish_request re-runs its supersede+INSERT
# transaction after losing a concurrent-create race on the pending unique
# index (#429 三轮 P1-1). Each retry supersede-matches the winner's row, so
# the second attempt succeeds barring pathological interleavings.
_CREATE_PENDING_MAX_ATTEMPTS = 3

_REQUEST_COLUMNS = (
    "id, workspace_id, chat_session_id, status, created_by,"
    " result_revision_id, draft_hash, created_at, expires_at, resolved_at"
)


class StudioPublishRequestQueriesMixin(
    StudioPublishRequestClaimQueriesMixin, ConnectionQueriesMixin
):
    """Create / supersede / lazy expiry / reads for the handshake. The
    confirm-race transitions (claim / resolve / reject / confirming read)
    arrive via the claims mixin (#429 三轮 P1 split, file budget)."""

    def create_pending_publish_request(
        self,
        workspace_id: str,
        created_by: str,
        chat_session_id: str | None = None,
        *,
        draft_hash: str | None = None,
        ttl_seconds: int = PUBLISH_REQUEST_TTL_SECONDS,
    ) -> dict[str, Any]:
        """Supersede any pending request and insert a fresh pending row.

        The supersede and the INSERT share one transaction; when a concurrent
        create wins the race for the workspace's single pending slot (partial
        unique index, #429 三轮 P1-1), this transaction's INSERT raises
        UniqueViolation and the whole thing re-runs — on the retry the
        supersede UPDATE matches the winner's row, so this request displaces
        it exactly like a sequential re-request would.
        """
        request_id = uuid4().hex
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        for _attempt in range(_CREATE_PENDING_MAX_ATTEMPTS):
            try:
                with self.connect() as conn:
                    conn.execute(
                        "update studio_publish_requests set status='superseded',"
                        " resolved_at=current_timestamp"
                        " where workspace_id=%s and status='pending'",
                        (workspace_id,),
                    )
                    row = conn.execute(
                        "insert into studio_publish_requests"
                        "(id, workspace_id, chat_session_id, created_by, draft_hash,"
                        " expires_at) values (%s, %s, %s, %s, %s, %s)"
                        " returning " + _REQUEST_COLUMNS,
                        (
                            request_id,
                            workspace_id,
                            chat_session_id,
                            created_by,
                            draft_hash,
                            expires_at,
                        ),
                    ).fetchone()
                if row is not None:
                    return dict(row)
                raise RuntimeError("publish request insert did not return a row")
            except UniqueViolation:
                # A concurrent create landed its pending row between this
                # transaction's supersede (matched zero rows) and its INSERT.
                # Retry: the supersede now matches that row (#429 三轮 P1-1).
                continue
        raise RuntimeError(
            "publish request create lost the pending-slot race "
            f"{_CREATE_PENDING_MAX_ATTEMPTS} times for workspace {workspace_id}"
        )

    def expire_pending_publish_request(self, workspace_id: str) -> dict[str, Any] | None:
        """Write-side lazy expiry for a workspace: if the pending row is past
        its TTL, flip it to ``expired`` and return it; None when there is no
        pending row or it is still within its TTL. The write-side
        counterpart of the pure pending read (#429: the 5s poll must not
        generate write transactions, so the read no longer sweeps)."""
        with self.connect() as conn:
            row = conn.execute(
                "update studio_publish_requests set status='expired',"
                " resolved_at=current_timestamp"
                " where workspace_id=%s and status='pending'"
                " and expires_at < current_timestamp"
                " returning " + _REQUEST_COLUMNS,
                (workspace_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_pending_publish_request(self, workspace_id: str) -> dict[str, Any] | None:
        """The workspace's pending request, None when there is none.

        A pure read (pooled connection, no write transaction, no TTL filter):
        the Studio frontend polls this every 5s, and the read must not turn
        the poll into write load — the old read-then-sweep design opened a
        write connection on EVERY poll (#429). A pending row past its
        ``expires_at`` still surfaces here; the service layer decides
        whether the row is expired (and only then writes, via
        ``expire_pending_publish_request``), so this read itself issues zero
        writes (#429 二轮复审 NIT：认证 session 的滑动过期仍会写，不在本
        路径的断言范围内).
        """
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

    def get_publish_request_current_state(self, request_id: str) -> dict[str, Any] | None:
        """One request by id as it stands right now — pure read, no expiry
        write. The losing side of the confirm race uses this to report the
        request's real terminal state (#429): when a confirm's publish
        landed but the resolve lost a race (superseded mid-publish), the
        row still reads back with its final status for the response."""
        with self._connect_read() as conn:
            row = conn.execute(
                f"select {_REQUEST_COLUMNS} from studio_publish_requests where id=%s",
                (request_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def supersede_pending_publish_requests(self, workspace_id: str) -> int:
        """Move the workspace's pending rows to ``superseded``; returns the
        displaced count. Used by the manual publish path (#429): once a
        human publishes through the toolbar button, any pending agent
        request for the same workspace is moot — its content went live, and
        the agent polling the status tool should see ``superseded`` (a
        publish happened, just not through this request) instead of a
        dead-end pending whose review dialog can never be usefully
        confirmed again. A row in ``confirming`` is deliberately left
        alone: its publish is in flight and will record its own outcome
        (#429 三轮 P1-2 — superseding it would recreate the cancel race in
        supersede form).
        """
        with self.connect() as conn:
            cursor = conn.execute(
                "update studio_publish_requests set status='superseded',"
                " resolved_at=current_timestamp"
                " where workspace_id=%s and status='pending'",
                (workspace_id,),
            )
            return cursor.rowcount
