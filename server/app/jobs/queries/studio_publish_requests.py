"""Persistence for agent-initiated workflow publish requests (schema v76).

The per-workspace single-pending invariant is a partial unique index
(``workspace_id where status='pending'``, #429 三轮 P1-1), not a
read-modify-write courtesy: ``create_pending_request`` supersede-then-INSERTs
in one transaction, and when a concurrent create already landed its pending
row, the loser's INSERT hits the index and the whole transaction retries —
on retry the supersede UPDATE matches the winner's row, so exactly one
pending row survives and the loser's request takes over the slot (the same
supersede semantics as sequential calls). The manual publish path displaces
pending rows the same way (``supersede_pending_publish_requests``, #429).

The CREATE-side confirming guard is IN the create transaction
(#429 四轮 codex P1): the guard, the supersede, and the INSERT share one
transaction held under a per-workspace advisory lock (``_lock_workspace``)
that the CLAIM also takes — so a create can never observe "no confirming"
while a claim is concurrently turning the old pending row INTO confirming
(the old two-transaction guard allowed exactly that: a second request
parked during the publish window, and after the first publish resolved the
user got a second dialog on the same stale draft). The claim
(``claim_pending_publish_request``, claims module) takes the same lock,
closing the window from both sides.

``confirming`` (#429 三轮 P1-2) is the claim state: the confirm endpoint
atomically moves pending→confirming before it publishes, so a cancel racing
the publish can no longer land ``rejected`` on a row whose revision is about
to go live. The claim/resolve transitions live in
studio_publish_request_claims.py; the pure reads in
studio_publish_request_reads.py; the lazy-expiry and manual-supersede
writes in studio_publish_request_writes.py (all split for file budget);
this module keeps create and the shared advisory lock.

Expiry is lazy — the pending read is pure (no sweep on the 5s poll path),
and the terminal ``expired`` row is recorded by the write-side
observed-expiry path (``expire_pending_publish_request`` in
studio_publish_request_writes.py; status read by id sweeps in the same
statement it reads with). Resolution NEVER checks ``expires_at``: the claim
step already checked TTL, and a resolve whose underlying publish already
landed must record the effect truthfully (a TTL expiring mid-publish must
not deny a revision that exists — TOCTOU, #429).

``confirming`` has its OWN recovery TTL (#429 四轮 P1): the claim stamps
``claimed_at``, and a confirming row older than
``CONFIRMING_STALE_SECONDS`` is a dead process's claim (deploy restart
between claim and resolve) — the create transaction sweeps it to
``expired`` in the same statement sequence (under the lock), so the
workspace is never wedged. The healthy poll stays write-free.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from psycopg.errors import UniqueViolation

from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.jobs.queries.studio_publish_request_claims import (
    _REQUEST_COLUMNS,
    StudioPublishRequestClaimQueriesMixin,
)
from server.app.jobs.queries.studio_publish_request_reads import (
    StudioPublishRequestReadQueriesMixin,
)
from server.app.jobs.queries.studio_publish_request_writes import (
    StudioPublishRequestWriteQueriesMixin,
)
from server.app.services.job_errors import ConflictError
from server.app.services.studio_publish_request_support import (
    CONFIRMING_STALE_SECONDS,
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

# Advisory-lock key namespace for the handshake's per-workspace critical
# section: create (guard + supersede + INSERT) and claim (pending→confirming)
# serialize on it (#429 四轮 codex P1). Any 63-bit constant works as long as
# it is unique to this table; hashtext(workspace_id) spreads the keys.
_PUBLISH_REQUEST_LOCK_NAMESPACE = 416429


class StudioPublishRequestQueriesMixin(
    StudioPublishRequestClaimQueriesMixin,
    StudioPublishRequestWriteQueriesMixin,
    StudioPublishRequestReadQueriesMixin,
    ConnectionQueriesMixin,
):
    """Create (with the in-transaction confirming guard, under the
    claim-shared advisory lock) for the handshake. The confirm-race
    transitions (claim / resolve / reject) arrive via the claims mixin, the
    lazy-expiry/supersede writes via the writes mixin, the pure reads via
    the reads mixin (#429 splits, file budget) — groups.py composes this
    one facade class."""

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

        One transaction, three statements, one advisory lock
        (#429 四轮 codex P1): (1) the confirming guard — a STALE confirming
        row (claim past the recovery TTL) is swept to ``expired`` so a dead
        process's claim cannot wedge the workspace, while a LIVE one refuses
        the create (the claim's publish is in flight; superseding it would
        recreate the cancel race in supersede form, #429 三轮 P1-2); (2) the
        supersede of the current pending row; (3) the INSERT. The lock is
        shared with ``claim_pending_publish_request``, so the guard's view of
        "is there a live confirming row" cannot change between the check and
        the INSERT — the two-transaction version let a claim interleave,
        parking a second request during the publish window. Concurrent
        creates still race on the pending unique index (#429 三轮 P1-1) and
        retry with supersede-matching semantics.
        """
        request_id = uuid4().hex
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        for _attempt in range(_CREATE_PENDING_MAX_ATTEMPTS):
            try:
                with self.connect() as conn:
                    _lock_workspace(conn, workspace_id)
                    # (1) The confirming guard, inside the transaction and
                    # under the claim-shared lock (#429 四轮 codex P1 + the
                    # stale-claim TTL recovery, #429 四轮 P1).
                    conn.execute(
                        "update studio_publish_requests set status='expired',"
                        " resolved_at=current_timestamp"
                        " where workspace_id=%s and status='confirming'"
                        " and claimed_at < current_timestamp"
                        f" - interval '{int(CONFIRMING_STALE_SECONDS)} seconds'",
                        (workspace_id,),
                    )
                    live = conn.execute(
                        "select 1 from studio_publish_requests"
                        " where workspace_id=%s and status='confirming' limit 1",
                        (workspace_id,),
                    ).fetchone()
                    if live is not None:
                        raise _LiveConfirmingRequestError()
                    # (2) Supersede the current pending row.
                    conn.execute(
                        "update studio_publish_requests set status='superseded',"
                        " resolved_at=current_timestamp"
                        " where workspace_id=%s and status='pending'",
                        (workspace_id,),
                    )
                    # (3) Insert the new pending row.
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
                raise ConflictError("publish request insert did not return a row")
            except UniqueViolation:
                # A concurrent create landed its pending row between this
                # transaction's supersede (matched zero rows) and its INSERT.
                # Retry: the supersede now matches that row (#429 三轮 P1-1).
                continue
        # #429 四轮 P3-1: JobServiceError, not a bare RuntimeError — the tool
        # route maps it to a 409 (the same "retry in a moment" semantics as
        # the confirming-window refusal), instead of an unhandled 500.
        raise ConflictError(
            "publish request create lost the pending-slot race "
            f"{_CREATE_PENDING_MAX_ATTEMPTS} times for workspace {workspace_id};"
            " retry the request"
        )


class _LiveConfirmingRequestError(ConflictError):
    """Raised INSIDE create's transaction when a live confirming row blocks
    the insert (#429 四轮 codex P1). Subclassing ConflictError keeps the
    route's 409 mapping; the dedicated type lets the retry loop distinguish
    "refused by a live claim" (do not retry — re-raise) from
    UniqueViolation (retry). Aborting through an exception rolls the
    transaction back, leaving the confirming row exactly as it was."""

    def __init__(self) -> None:
        super().__init__(
            "The previous publish request is being confirmed right now;"
            " re-request after it resolves"
        )


def _lock_workspace(conn: Any, workspace_id: str) -> None:
    """Take the handshake's per-workspace advisory lock (transaction-scoped:
    released at COMMIT/ROLLBACK). Serializes create's guard+supersede+INSERT
    against the claim's pending→confirming transition (#429 四轮 codex P1).
    The namespace constant keeps the key disjoint from the schema-migration
    advisory lock (schema.py) and any other lock in the deployment."""
    conn.execute(
        "select pg_advisory_xact_lock(%s, hashtext(%s))",
        (_PUBLISH_REQUEST_LOCK_NAMESPACE, workspace_id),
    )
