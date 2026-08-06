"""Empty-claim restock trigger for the Agent execution queue.

A Worker claim that finds no work returns None; when the queue is truly
empty that is a demand signal the producer side should react to immediately
instead of waiting out the poll loop's idle backoff. Dozens of Workers can
hit empty claims in a burst, so the signal is debounced: at most one
restock callback per ``debounce_seconds``, and the expensive production
side stays bounded by the stockpile gate and the enqueue pool.

Split out of ``broker.py`` so the broker module stays within its size
budget; mirrors the sibling module layout.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping

from server.app.agent_broker.empty_diagnostics import log_blocked_queue
from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection

logger = logging.getLogger(__name__)


class EmptyClaimTrigger:
    """Coalesce bursts of empty Worker claims into a throttled restock signal."""

    def __init__(self, debounce_seconds: float = 5.0) -> None:
        self.debounce_seconds = debounce_seconds
        # Wired by the app lifespan to the workflow worker's restock hook.
        self.on_empty_queue: Callable[[], None] | None = None
        self._lock = threading.Lock()
        self._last_fired = 0.0

    def note_empty_claim(
        self, dsn: DatabaseDsn, *, skip_reasons: Mapping[str, int] | None = None
    ) -> None:
        """Act on an empty claim: restock on true demand, warn on blockage.

        Debounced by wall clock: a burst of empty claims collapses into one
        probe + callback per interval. The probe separates the two cases —
        no queued rows remain means true demand (fire the restock callback);
        queued rows with skip reasons means a blocked queue (log the reason
        histogram, see ``empty_diagnostics.py``) and must not restock.
        """
        callback = self.on_empty_queue
        reasons = {key: count for key, count in (skip_reasons or {}).items() if count}
        if callback is None and not reasons:
            return
        with self._lock:
            now = time.monotonic()
            if now - self._last_fired < self.debounce_seconds:
                return
            self._last_fired = now
        with read_connection(dsn) as conn:
            row = conn.execute(
                "select 1 from agent_execution_requests where state='queued' limit 1"
            ).fetchone()
            if row is not None:
                if reasons:
                    log_blocked_queue(dsn, conn, reasons)
                return
        if callback is None:
            return
        try:
            callback()
        except Exception:
            logger.exception("empty-queue restock callback failed")
