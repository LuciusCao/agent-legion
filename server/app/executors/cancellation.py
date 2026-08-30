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

# The token lives in workspace_libs (execution plane, zero server.app imports)
# so the sandboxed code child and worker runners can use it; this module
# re-exports it for Host-side callers and keeps the Host-only helpers.
from workspace_libs.cancellation import CancellationToken, CancelledError

__all__ = [
    "CancelledError",
    "CancellationToken",
    "SubprocessTracker",
    "check_cancellation",
]

logger = logging.getLogger(__name__)


def check_cancellation(runtime: Mapping[str, object] | None) -> None:
    """Raise ``CancelledError`` when a cancellation token in *runtime* is set."""
    token = (runtime or {}).get("cancellation")
    if isinstance(token, CancellationToken):
        token.raise_if_cancelled()


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
            # The child (or its group) was reaped between the poll above and
            # the signal — the exact outcome the terminate wanted.
            pass
        except Exception:
            # #204 broad-except audit: last-resort termination safety net.
            # This runs on the cancellation path (worker shutdown / node
            # cancel), where the caller's own outcome must not be replaced by
            # a teardown error — the escalation is best-effort by design and
            # an un-killable child is surfaced via the logged traceback (and
            # the caller's wait deadline) rather than by masking the cancel.
            # The narrow races (already-dead child) are handled above; what
            # lands here is e.g. PermissionError from a recycled pgid, which
            # has no better classification.
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
