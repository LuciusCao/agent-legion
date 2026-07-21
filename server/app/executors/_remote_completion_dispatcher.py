"""Dedicated dispatcher thread for remote completion callbacks."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.app.executors.remote_broker import RemoteOutcome

logger = logging.getLogger(__name__)

_Callback = Callable[[str, "RemoteOutcome"], None]
_Item = tuple[_Callback, str, "RemoteOutcome"]


class CompletionDispatcher:
    """Serialize completion callbacks on one daemon thread without dropping work."""

    def __init__(self, maxsize: int = 256) -> None:
        self._queue: queue.Queue[_Item | None] = queue.Queue(maxsize=maxsize)
        self._closed = False
        self._close_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._loop,
            name="remote-completion-dispatcher",
            daemon=True,
        )
        self._thread.start()

    def dispatch(self, callback: _Callback, execution_id: str, outcome: RemoteOutcome) -> None:
        """Queue a callback, applying backpressure rather than dropping it."""
        with self._close_lock:
            if self._closed:
                raise RuntimeError("completion dispatcher is closed")
            self._queue.put((callback, execution_id, outcome))

    def wait_idle(self) -> None:
        """Block until every queued callback has run."""
        self._queue.join()

    def close(self, timeout: float = 5.0) -> None:
        """Drain queued work and stop the dispatcher thread."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self._queue.put(None)
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            logger.warning("remote completion dispatcher did not stop within %.1fs", timeout)

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                callback, execution_id, outcome = item
                try:
                    callback(execution_id, outcome)
                except Exception:
                    logger.exception("remote completion callback failed for %s", execution_id)
            finally:
                self._queue.task_done()
