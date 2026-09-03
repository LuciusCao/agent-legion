"""The poll-side read of the agent publish-request handshake (#429 四轮).

Split from services/studio_publish_requests.py (file budget: the poll
orchestration — pending read, live-confirming surfacing, the lazy sweeps —
outgrew the service's ceiling). This module owns exactly one question:
"what should the Studio frontend's 5s poll see right now?" The answer is a
state machine over the workspace's single request slot:

- the ``pending`` row (the review dialog's content), swept to ``expired``
  first when it is past its TTL;
- the ``confirming`` row while its publish is in flight (#429 四轮 P3-2 —
  the dialog stays open showing "publish in progress"), unless the claim
  is STALE (#429 四轮 P1 — a dead process's claim, swept to ``expired``);
- None when the slot is empty.

The new-request path's stale-claim sweep lives inside its own transaction
(queries layer, under the claim-shared advisory lock, #429 四轮 codex P1);
this module's sweep only serves the poll read of a workspace whose agent
is not re-requesting.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from server.app.services.studio_publish_request_support import (
    CONFIRMING_STALE_SECONDS,
    is_past_expiry,
    is_stale_claim,
    iso_payload,
)

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)


def poll_pending_request(job_db: JobQueries, workspace_id: str) -> dict[str, Any] | None:
    """The workspace's live request for the poll (None when there is none):
    the pending row, or — #429 四轮 P3-2 — the ``confirming`` row while its
    publish is in flight. The poll is what keeps the review dialog open; a
    confirm that outlives one 5s poll cycle used to make the row vanish
    (pending-only read), closing the dialog mid-publish and landing a bogus
    "resolved away" receipt in the observer effect. Surfacing the confirming
    row (with its status) keeps the dialog up showing "publish in progress";
    the dialog resolves the receipt only on a genuine pending→null
    transition (the confirming row is not null, so no fake jump fires).

    Read first, sweep only on observed expiry (#429): the pure read returns
    the pending row regardless of TTL; when that row is past its
    ``expires_at`` one write records the terminal ``expired`` state (so the
    agent's status tool and any later confirm see a terminal row, not a
    zombie pending) and the poll answers None — the dialog's "request is
    gone, close" signal. A healthy workspace polls with one read connection
    and this path itself opens zero write connections (the auth session's
    sliding expiry still writes — that is outside this path, not a claim
    about the request store); the old design opened a write on every 5s poll.
    """
    request = job_db.get_pending_publish_request(workspace_id)
    if request is not None:
        if is_past_expiry(request):
            # Best effort: a racing resolution just means the row is already
            # terminal. Either way the poll's answer is "no pending request".
            job_db.expire_pending_publish_request(workspace_id)
            return None
        return iso_payload(request)
    # No pending row: surface a LIVE confirming row (#429 四轮 P3-2) — the
    # in-flight confirm keeps the dialog open; but a STALE one is a dead
    # process's claim: sweep it to ``expired`` (#429 四轮 P1) and answer None
    # (the dead confirm resolved nothing).
    confirming = job_db.get_confirming_publish_request(workspace_id)
    if confirming is not None and is_stale_claim(confirming):
        _sweep_stale_confirming(job_db, workspace_id)
        return None
    if confirming is not None:
        return iso_payload(confirming)
    return None


def _sweep_stale_confirming(job_db: JobQueries, workspace_id: str) -> None:
    """Flip the workspace's stale confirming row to ``expired`` (best
    effort; the predicate re-checks staleness inside the statement)."""
    swept = job_db.expire_stale_confirming_publish_request(workspace_id)
    if swept is not None:
        logger.warning(
            "expired stale confirming publish request %s for workspace %s (claim older than %ss)",
            swept["id"],
            workspace_id,
            CONFIRMING_STALE_SECONDS,
        )
