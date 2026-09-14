"""pidfd-mode plumbing for the exit watcher (#647 — #578 phase 2).

Split out of ``worker/execution/exit_watch.py`` for the file-size budget,
not for layering: these helpers are the Linux pidfd arm of the watcher —
the deferred-registration queue application (selector mutations and fd
closes are watcher-thread-only; closing an fd another thread selects on is
the hazard) and the fd-drain used by both death paths (``_fail_dead`` and
``shutdown`` — the watcher thread is gone there, so the close-anywhere
hazard no longer applies).
"""

from __future__ import annotations

import contextlib
import os
import selectors
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from worker.execution.exit_watch import _Waiter


def apply_pending(
    pending: list[tuple[str, _Waiter]],
    waiters: dict[int, _Waiter],
    lock: threading.Lock,
    selector: selectors.BaseSelector,
) -> None:
    """Watcher-thread-only: drain the deferred register/unregister queue."""
    for op, waiter in pending:
        if waiters.get(waiter.proc.pid) is not waiter and op == "register":
            continue  # unregistered while queued — nothing to arm
        if op == "register":
            try:
                fd = os.pidfd_open(waiter.proc.pid)  # type: ignore[attr-defined]
            except OSError:
                # Raced a reaped child (heartbeat poll) or unsupported —
                # resolved now if it already exited, else tick-polled.
                with lock:
                    if waiter.proc.poll() is not None:
                        waiter.exit_observed = True
                        waiter.done.set()
                    else:
                        waiter.scan_only = True
                continue
            waiter.pidfd = fd
            selector.register(fd, selectors.EVENT_READ, data=waiter)
        else:  # unregister
            release_pidfd(waiter, selector)


def release_pidfd(waiter: _Waiter, selector: selectors.BaseSelector | None) -> None:
    """Drop one waiter's pidfd from the selector and close it."""
    fd = waiter.pidfd
    waiter.pidfd = -1
    if fd >= 0 and selector is not None:
        with contextlib.suppress(KeyError, ValueError):
            selector.unregister(fd)
        with contextlib.suppress(OSError):
            os.close(fd)


def drain_pidfds(
    waiters: list[_Waiter],
    pending: list[tuple[str, _Waiter]],
    selector: selectors.BaseSelector | None,
) -> None:
    """Death-path drain: close every applied and queued-but-unapplied pidfd
    (one fd per in-flight execution would otherwise leak per reactor death,
    and a fresh singleton spawns on each death, so the leak accumulates)."""
    for waiter in waiters:
        release_pidfd(waiter, selector)
    for _op, waiter in pending:
        release_pidfd(waiter, selector)
