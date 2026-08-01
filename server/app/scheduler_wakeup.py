"""Process-local wakeup registry for the workflow scheduler.

Any write path that may produce newly schedulable workflow nodes (job intake,
rerun, ...) calls ``notify_schedulable_work`` so the worker's poll loop wakes
immediately instead of waiting out its idle backoff. The worker registers its
``wake`` callback from the application lifespan and unregisters on shutdown.
Callbacks are invoked best-effort: a failing callback is logged and never
affects the caller or the other callbacks.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_callbacks: list[Callable[[], None]] = []


def register_wakeup(callback: Callable[[], None]) -> None:
    """Register *callback* to be invoked by ``notify_schedulable_work``."""
    with _lock:
        if callback not in _callbacks:
            _callbacks.append(callback)


def unregister_wakeup(callback: Callable[[], None]) -> None:
    """Remove a previously registered callback; missing callbacks are ignored."""
    with _lock:
        if callback in _callbacks:
            _callbacks.remove(callback)


def notify_schedulable_work() -> None:
    """Invoke all registered wakeup callbacks; never raises."""
    with _lock:
        callbacks = list(_callbacks)
    for callback in callbacks:
        try:
            callback()
        except Exception:
            logger.exception("scheduler wakeup callback %r failed", callback)
