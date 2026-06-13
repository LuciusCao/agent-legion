from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class CancelledError(Exception):
    """Raised when code observes an active cancellation request."""


def check_cancellation(runtime: Mapping[str, object] | None) -> None:
    """Raise ``CancelledError`` when a cancellation token in *runtime* is set."""
    token = (runtime or {}).get("cancellation")
    if isinstance(token, CancellationToken):
        token.raise_if_cancelled()


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


@dataclass
class SubprocessTracker:
    """Tracks active subprocesses and performs bounded process-group termination."""

    grace_seconds: float = 5.0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _processes: dict[str, subprocess.Popen[Any]] = field(default_factory=dict)

    def register(self, execution_id: str, process: subprocess.Popen[Any]) -> None:
        with self._lock:
            self._processes[execution_id] = process

    def unregister(self, execution_id: str) -> None:
        with self._lock:
            self._processes.pop(execution_id, None)

    def active(self) -> list[str]:
        with self._lock:
            return list(self._processes)

    def cancel(self, execution_id: str) -> None:
        """Request cancellation for *execution_id* with bounded force."""
        with self._lock:
            process = self._processes.pop(execution_id, None)
        if process is None:
            return
        self._terminate(process)

    def _terminate(self, process: subprocess.Popen[Any]) -> None:
        if process.poll() is not None:
            return
        try:
            if hasattr(os, "killpg"):
                try:
                    pgid = os.getpgid(process.pid)
                    os.killpg(pgid, signal.SIGTERM)
                except ProcessLookupError:
                    process.terminate()
            else:
                process.terminate()
            try:
                process.wait(timeout=self.grace_seconds)
                return
            except subprocess.TimeoutExpired:
                pass

            if hasattr(os, "killpg"):
                try:
                    pgid = os.getpgid(process.pid)
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    process.kill()
            else:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Subprocess %s did not exit after SIGKILL", process.pid)
        except ProcessLookupError:
            pass
        except Exception:
            logger.exception("Failed to terminate subprocess %s", process.pid)

    def wait_for(self, execution_id: str, timeout: float | None = None) -> bool:
        """Wait up to *timeout* seconds for the tracked process to exit."""
        with self._lock:
            process = self._processes.get(execution_id)
        if process is None:
            return True
        deadline = None if timeout is None else time.monotonic() + timeout
        while process.poll() is None:
            if deadline is not None and time.monotonic() > deadline:
                return False
            time.sleep(0.05)
        return True
