"""Watchdog admission helpers kept separate from its lifecycle core."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from server.app.studio_chat.turn_watchdog_core import TurnWatchdog

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime


def begin_turn_admission(runtime: SessionRuntime) -> object:
    token = object()
    runtime.turn_admission = token
    return token


def cancel_turn_admission(runtime: SessionRuntime, token: object) -> None:
    if getattr(runtime, "turn_admission", None) is token:
        runtime.turn_admission = None
    pending = getattr(runtime, "pending_cancel", None)
    if pending is not None and pending[0] is token:
        runtime.pending_cancel = None


def arm_turn_watchdog(runtime: SessionRuntime) -> None:
    """Caller holds runtime.lock after open_turn has stamped its owner."""
    handle = runtime.handle
    callbacks = getattr(handle, "callbacks", None)
    previous = runtime.turn_watchdog
    if previous is not None:
        previous.disarm()
    runtime.turn_watchdog = None
    pending = getattr(runtime, "pending_cancel", None)
    runtime.pending_cancel = None
    if getattr(callbacks, "runtime", None) is not runtime:
        return
    if (process := getattr(handle, "_process", None)) is not None:
        watchdog = TurnWatchdog(runtime, runtime.turn_owner, process)
        watchdog.arm()
        if pending is not None and pending[0] is runtime.turn_owner:
            watchdog.request_cancel(pending[1])


def note_prompt_response(handle: Any) -> None:
    runtime = getattr(handle.callbacks, "runtime", None)
    if runtime is None or runtime.turn_watchdog is None:
        return
    runtime.turn_watchdog.note_response()


def request_user_cancel(runtime: SessionRuntime) -> bool:
    if runtime.closed:
        return False
    watchdog = runtime.turn_watchdog
    if watchdog is not None and watchdog.callbacks.runtime is runtime and runtime.turn_open:
        return bool(watchdog.request_cancel())
    token = getattr(runtime, "turn_admission", None)
    if token is None:
        return False
    if getattr(runtime, "pending_cancel", None) is None:
        runtime.pending_cancel = (token, time.monotonic())
    return True


def cancel_requested(runtime: SessionRuntime) -> bool:
    if getattr(runtime, "pending_cancel", None) is not None:
        return True
    watchdog = runtime.turn_watchdog
    if watchdog is None:
        return False
    with watchdog._timer_lock:
        return watchdog._cancel_deadline is not None


def transport_failed(handle: Any) -> bool:
    if getattr(handle, "_watchdog_candidate", None) is not None:
        return True
    return isinstance(getattr(handle, "transport_failure", lambda: None)(), str)


def disarm_turn_watchdog(runtime: SessionRuntime, owner: object | None = None) -> None:
    watchdog = runtime.turn_watchdog
    if watchdog is not None:
        watchdog.disarm(owner)
    runtime.turn_admission = runtime.pending_cancel = None
