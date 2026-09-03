"""The confirm-race transitions of studio_publish_requests (#429 三轮 P1).

Split from studio_publish_requests.py (file budget: the row lifecycle's
claim/resolve half outgrew the shared module's committed ceiling): the
transitions here are the ones the confirm endpoint drives — the atomic
``confirming`` claim (with TTL + draft-hash predicates in the same update
statement), the confirming-only resolve (success, refused-publish rollback,
and cancel's pending-only reject), and the confirm-window read the create
path refuses new requests with. The create/supersede/expiry/read halves stay
in the original module; both mixins compose into the same JobQueries facade
(groups.py).
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin

_REQUEST_COLUMNS = (
    "id, workspace_id, chat_session_id, status, created_by,"
    " result_revision_id, draft_hash, created_at, expires_at, resolved_at"
)


class StudioPublishRequestClaimQueriesMixin(ConnectionQueriesMixin):
    """pending→confirming→{confirmed|pending|rejected}: the confirm path."""

    def claim_pending_publish_request(
        self, workspace_id: str, request_id: str, draft_hash: str | None
    ) -> dict[str, Any] | None:
        """Atomically move the workspace's pending row to ``confirming``
        (#429 三轮 P1-2/P1-3). This is the pre-publish claim: after it
        returns, cancel can no longer touch the row (its predicate matches
        pending only), and a new agent request during the publish window is
        refused instead of superseding. The claim ALSO re-checks the draft
        hash in the same UPDATE — the row only moves to confirming when the
        server draft is still the one the agent requested (hash mismatch →
        None, the confirm surfaces a 409 instead of publishing a draft the
        human never reviewed). None when the row is missing, not pending,
        not the named request, past its TTL, or the draft drifted."""
        with self.connect() as conn:
            row = conn.execute(
                "update studio_publish_requests set status='confirming'"
                " where id=%s and workspace_id=%s and status='pending'"
                " and expires_at >= current_timestamp and draft_hash is not distinct from %s"
                " returning " + _REQUEST_COLUMNS,
                (request_id, workspace_id, draft_hash),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_confirming_publish_request(self, workspace_id: str) -> dict[str, Any] | None:
        """The workspace's confirming row, None when there is none. The create
        path's re-request refusal (#429 三轮 P1-2): a new pending row must
        not displace a row whose publish is in flight."""
        with self._connect_read() as conn:
            row = conn.execute(
                f"select {_REQUEST_COLUMNS} from studio_publish_requests"
                " where workspace_id=%s and status='confirming'"
                " order by created_at desc limit 1",
                (workspace_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def resolve_publish_request(
        self,
        request_id: str,
        *,
        status: str,
        result_revision_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Atomically finish a claimed (``confirming``) request; None when
        the request is missing or no longer confirming (already resolved,
        or lost a racing resolution).

        The status predicate rides the same UPDATE as the write, so two
        concurrent confirm/cancel calls cannot both win: the loser sees no
        row and gets None instead of double-resolving. The claim state is
        also the retry loop's reset target — a refused publish moves the
        row back to ``pending`` through this same entry point.

        Deliberately NO ``expires_at`` predicate (#429 TOCTOU): the caller
        checked TTL when it claimed the row, and the work between claim and
        resolve (a full publish) may legitimately outlive the remaining TTL.
        If the publish landed, the state machine must say ``confirmed`` —
        an expiry that fires mid-publish must not deny a revision that
        exists on disk.
        """
        if status not in ("confirmed", "rejected", "pending"):
            raise ValueError(f"unsupported resolve status: {status}")
        if status == "pending" and result_revision_id is not None:
            # The retry loop's reset must not inherit any revision receipt.
            raise ValueError("result_revision_id cannot ride a pending reset")
        with self.connect() as conn:
            row = conn.execute(
                # pending (retry loop) re-opens the request: resolved_at must
                # not keep the refused attempt's timestamp — CASE keeps the
                # reset in the same single-statement atomicity.
                "update studio_publish_requests set status=%s,"
                " result_revision_id=%s,"
                " resolved_at=case when %s='pending' then null else current_timestamp end"
                " where id=%s and status='confirming'"
                " returning " + _REQUEST_COLUMNS,
                (status, result_revision_id, status, request_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def reject_pending_publish_request(
        self, workspace_id: str, request_id: str, *, status: str
    ) -> dict[str, Any] | None:
        """Atomically move a PENDING row to a terminal state (cancel's
        resolve, #429 三轮 P1-2). The predicate matches ``pending`` only —
        a row in ``confirming`` (its publish in flight) is untouchable, so
        a cancel racing the publish cannot write ``rejected`` over a
        revision that is about to be live. None when the row is missing,
        not pending, or not the workspace's current pending row."""
        if status != "rejected":
            raise ValueError(f"unsupported reject status: {status}")
        with self.connect() as conn:
            row = conn.execute(
                "update studio_publish_requests set status=%s,"
                " resolved_at=current_timestamp"
                " where id=%s and workspace_id=%s and status='pending'"
                " returning " + _REQUEST_COLUMNS,
                (status, request_id, workspace_id),
            ).fetchone()
        return dict(row) if row is not None else None
