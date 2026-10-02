"""Cancellation rearming linearizes after durable human-message acceptance."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime

logger = logging.getLogger(__name__)


def try_rearm(runtime: SessionRuntime) -> bool:
    """Caller holds runtime.lock; failed observation keeps automatic turns disabled."""
    if runtime.background_rearm_epoch != runtime.background_epoch or runtime.closed:
        return False
    try:
        if runtime.background_cursor is not None:
            runtime.background_cursor.baseline()
    except Exception:
        # #204 broad-except audit: human input is already durable. A failed
        # baseline must not abort its queue handoff or enable stale followups.
        # Retain the rearm request for watcher retry and log the traceback.
        logger.warning("Kimi rearm baseline failed", exc_info=True)
        return False
    runtime.background_rearm_epoch = None
    runtime.background_wakeup_enabled = True
    return True


def prepare_rearm(runtime: SessionRuntime) -> Callable[[], None]:
    """Capture only cancellation identity; never cache pre-acceptance task state."""
    with runtime.lock:
        epoch = runtime.background_epoch

    def commit() -> None:
        with runtime.lock:
            if runtime.background_epoch == epoch and not runtime.background_wakeup_enabled:
                runtime.background_rearm_epoch = epoch
                try_rearm(runtime)

    return commit


def rearm_wakeup(runtime: SessionRuntime) -> None:
    prepare_rearm(runtime)()
