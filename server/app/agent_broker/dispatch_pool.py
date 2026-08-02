"""Bounded background pool for Agent enqueue staging and bundling.

``AgentDispatchService.enqueue`` stages input artifacts and builds a tar.gz
bundle synchronously (~1s per node); on the workflow worker's single poll
thread that serialized the whole claim loop once thousands of nodes were
ready. The poll thread therefore only submits the closure here and moves on;
the broker's unique (job, node) index remains the authoritative dedup when a
slow submission races the next pass.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class AgentEnqueuePool:
    """Fixed daemon workers draining enqueue closures; drops nothing silently."""

    def __init__(self, workers: int = 4, max_pending: int = 256) -> None:
        self._queue: queue.Queue[Callable[[], None] | None] = queue.Queue(max_pending)
        for index in range(workers):
            threading.Thread(target=self._run, name=f"agent-enqueue-{index}", daemon=True).start()

    def submit(self, fn: Callable[[], None]) -> bool:
        """Queue ``fn``; False when the backlog is full (retry next pass)."""
        try:
            self._queue.put_nowait(fn)
        except queue.Full:
            return False
        return True

    def _run(self) -> None:
        while True:
            fn = self._queue.get()
            if fn is None:
                return
            try:
                fn()
            except Exception:
                # The candidate has no broker row, so the next poll pass
                # re-evaluates and resubmits it; the log is the only signal.
                logger.exception("background agent enqueue failed")
