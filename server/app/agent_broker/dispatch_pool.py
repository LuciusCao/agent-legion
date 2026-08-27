"""Bounded background pool for Agent enqueue staging and bundling.

Each closure stages artifacts and builds a tar.gz bundle (~1s), too slow
for the workflow worker's single poll thread, which only submits here;
the broker's unique (job, node) index stays the authoritative dedup.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

# Tuning model lives in the neutral configuration module (issue #188);
# re-exported here for the existing broker-side consumers.
from server.app.configuration.executor_knobs import AgentEnqueueConfig as AgentEnqueueConfig

logger = logging.getLogger(__name__)


class AgentEnqueuePool:
    """Fixed daemon workers draining enqueue closures; drops nothing silently."""

    def __init__(self, workers: int = 16, max_pending: int = 1024) -> None:
        self._queue: queue.Queue[Callable[[], None] | None] = queue.Queue(max_pending)
        self._threads = [
            threading.Thread(target=self._run, name=f"agent-enqueue-{index}", daemon=True)
            for index in range(workers)
        ]
        for thread in self._threads:
            thread.start()

    def close(self) -> None:
        """Stop the worker threads (app shutdown); pending closures are dropped."""
        for _ in self._threads:
            self._queue.put(None)
        for thread in self._threads:
            thread.join(timeout=5)

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
