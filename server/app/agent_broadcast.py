from __future__ import annotations

import threading
from collections.abc import Callable


class AgentBroadcastController:
    """Batches agent status updates so small changes don't each force a broadcast."""

    def __init__(
        self,
        lock: threading.Lock,
        broadcast: Callable[[], None],
    ) -> None:
        self._lock = lock
        self._broadcast = broadcast
        self._broadcast_pending = False

    def has_pending_broadcast(self) -> bool:
        with self._lock:
            return self._broadcast_pending

    def flush_pending_broadcast(self) -> None:
        with self._lock:
            pending = self._broadcast_pending
            self._broadcast_pending = False
        if pending:
            self._broadcast()

    def mark_broadcast_pending(self) -> None:
        with self._lock:
            self._broadcast_pending = True
