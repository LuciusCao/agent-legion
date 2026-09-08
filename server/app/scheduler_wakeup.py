"""Process-local wakeup registry for the workflow scheduler.

Write paths that may produce schedulable work call
``notify_schedulable_work`` so the worker's poll loop wakes instead of
waiting out its idle backoff; the worker registers its ``wake`` callback
from the lifespan. Callbacks are best-effort (logged, never raise).
#521 方案 B: the optional cross-process backend (PostgreSQL NOTIFY) is
installed on HTTP-plane processes only; ``notify_local_wakeups`` is the
listener-side entry that skips it (loop guard).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_callbacks: list[Callable[[], None]] = []

# Cross-process notify backend (see module docstring); installed once at
# composition time via set_notify_backend, cleared via clear_notify_backend.
_notify_backend: Callable[[], None] | None = None


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


def set_notify_backend(backend: Callable[[], None] | None) -> None:
    """Install/clear the cross-process notify backend (None = clear)."""
    global _notify_backend
    with _lock:
        _notify_backend = backend


def notify_local_wakeups() -> None:
    """Invoke ONLY the local callbacks (never the backend).

    Listener-side counterpart of ``notify_schedulable_work``: the http
    plane listens on the very channel its backend emits to, so the full
    dispatch here would loop into a NOTIFY storm (#521 review P0)."""
    _invoke_callbacks()


def notify_schedulable_work() -> None:
    """Invoke all registered wakeup callbacks and the notify backend; never raises."""
    _invoke_callbacks()
    with _lock:
        backend = _notify_backend
    if backend is not None:
        try:
            backend()
        except Exception:
            # #204 broad-except audit: the cross-process backend is
            # scheduler_notify's emitter, which already contains its own
            # failure surface; this guard covers a replaced backend
            # breaking its contract. Same rationale as the callback
            # containment: never raises, poll backoff is the fallback.
            logger.exception("scheduler notify backend failed")


def _invoke_callbacks() -> None:
    with _lock:
        callbacks = list(_callbacks)
    for callback in callbacks:
        try:
            callback()
        except Exception:
            # #204 broad-except audit: per-callback containment on a
            # fire-and-forget notification. The callbacks are arbitrary
            # wake hooks registered by the worker threads — their outcome
            # space is whatever each hook touches, not a family this module
            # could enumerate. One failing callback must neither 500 the
            # write path that produced schedulable work nor block the
            # remaining callbacks (the module contract is "never raises",
            # and the poll loop's idle backoff is the fallback latency —
            # worst case the wake is lost and the next poll interval
            # rediscovers the work). logger.exception keeps the traceback.
            logger.exception("scheduler wakeup callback %r failed", callback)


def reload_scan_entries_best_effort(worker: Any) -> None:
    """Hot-reload the worker scan list; log instead of raising.

    Routes call this after a workspace scan target commits (workspace create,
    re-key, first publish); a transient failure must not 500 the write — the
    next reload or restart converges the list.
    """
    try:
        worker.reload_scan_entries()
    except Exception:
        # #204 broad-except audit: best-effort convergence, called inline on
        # the workspace write routes AFTER the commit. reload_scan_entries
        # re-reads workspace definitions (DB surface); a transient failure
        # must not 500 a write that already succeeded, and the state
        # self-heals — the next reload (another scan-target commit) or the
        # process restart converges the scan list. logger.exception keeps
        # the traceback so the divergence window is diagnosable.
        logger.exception("workflow scan-list hot reload failed")


def reload_worker_scan_entries(request: Any) -> None:
    """Reload via the app-state worker; on the http plane (#521 方案 B)
    the reload crosses the NOTIFY bridge instead — rationale in
    ``scheduler_notify_emit.bridge_scan_reload``."""
    worker = getattr(request.app.state, "workflow_worker", None)
    if worker is not None:
        reload_scan_entries_best_effort(worker)
    else:
        from server.app.scheduler_notify_emit import bridge_scan_reload

        bridge_scan_reload(request)
