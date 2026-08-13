"""Execution-plane cancellation primitives shared by Host and Worker.

The token itself lives in ``workspace_libs`` (not ``server.app``) because the
sandboxed code child (``workspace_libs.code_child``) and worker-side runners
must stay import-clean of ``server.app.*``. ``server.app.executors.
cancellation`` re-exports these names; Host-side helpers (``SubprocessTracker``,
``check_cancellation``) stay there.
"""

from __future__ import annotations

import threading
from typing import Any


class CancelledError(Exception):
    """Raised when code observes an active cancellation request."""


class CancellationToken:
    """Thread-safe and process-safe cancellation signal.

    When an optional *event* is supplied it is used as the backing primitive,
    which allows a ``multiprocessing.Event`` to be shared with an isolated
    child process.  Otherwise a lightweight ``threading.Event`` is used.
    """

    def __init__(self, event: Any | None = None) -> None:
        self._event = event or threading.Event()

    def is_cancelled(self) -> bool:
        return bool(self._event.is_set())

    def wait(self, timeout: float | None = None) -> bool:
        return bool(self._event.wait(timeout))

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise CancelledError("execution was cancelled")

    def cancel(self) -> None:
        self._event.set()
