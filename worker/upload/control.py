"""Small control primitives shared by the upload queue and transfer retries."""

from __future__ import annotations

import threading
import time
from typing import Literal

BulkOutcome = Literal["ready", "aborted", "lost"]


class CombinedStop:
    """Event-like signal that wakes for Worker shutdown or lease loss."""

    def __init__(self, shutdown: threading.Event, ownership_lost: threading.Event) -> None:
        self._shutdown = shutdown
        self._ownership_lost = ownership_lost

    def is_set(self) -> bool:
        return self._shutdown.is_set() or self._ownership_lost.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        # Independent Events cannot share a native waiter. Polling at 100ms
        # bounds lost-verdict latency even during a 60s transfer backoff.
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return False
            self._shutdown.wait(0.1 if remaining is None else min(0.1, remaining))
        return True
