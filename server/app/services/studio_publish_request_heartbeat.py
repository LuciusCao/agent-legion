"""Confirm-claim liveness for agent-initiated workflow publish requests."""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)

CONFIRMING_STALE_SECONDS = 300
_CLAIM_HEARTBEAT_INTERVAL_SECONDS = CONFIRMING_STALE_SECONDS / 5


@contextmanager
def confirming_claim_heartbeat(job_db: JobQueries, request_id: str):
    """Renew a confirming claim throughout gates, publish, and resolution.

    Renewal failures are logged without interrupting the publish. The row
    predicate stops the thread once it is no longer confirming. A live
    but indefinitely hung publisher therefore keeps its claim until process
    restart or a publish-internal timeout; this layer has no watchdog kill.
    """
    stop = threading.Event()
    thread = threading.Thread(
        target=_heartbeat_loop,
        args=(job_db, request_id, stop),
        name=f"publish-request-heartbeat-{request_id}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=_CLAIM_HEARTBEAT_INTERVAL_SECONDS + 5)


def _heartbeat_loop(job_db: JobQueries, request_id: str, stop: threading.Event) -> None:
    while not stop.wait(_CLAIM_HEARTBEAT_INTERVAL_SECONDS):
        try:
            if not job_db.heartbeat_confirming_publish_request(request_id):
                return
        except Exception:
            # #204 broad-except audit: a transient renewal failure must not
            # kill the daemon thread or fail the publish it merely protects.
            logger.exception("publish-request heartbeat failed for %s (continuing)", request_id)


def is_stale_claim(request: dict[str, Any]) -> bool:
    """Treat only an old, timestamped confirming claim as dead."""
    claimed_at = request.get("claimed_at")
    if claimed_at is None:
        return False
    if isinstance(claimed_at, str):
        claimed_at = datetime.fromisoformat(claimed_at)
    return claimed_at <= datetime.now(UTC) - timedelta(seconds=CONFIRMING_STALE_SECONDS)
