"""Watchdog admission helpers kept separate from its lifecycle core."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.studio_chat.turn_watchdog_core import TurnWatchdog

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime


def arm_turn_watchdog(runtime: SessionRuntime) -> None:
    """Caller holds runtime.lock after open_turn has stamped its owner."""
    handle = runtime.handle
    callbacks = getattr(handle, "callbacks", None)
    previous = runtime.turn_watchdog
    if previous is not None:
        previous.disarm()
    runtime.turn_watchdog = None
    if getattr(callbacks, "runtime", None) is not runtime:
        return
    if (process := getattr(handle, "_process", None)) is not None:
        TurnWatchdog(runtime, runtime.turn_owner, process).arm()


def note_prompt_response(handle: Any) -> None:
    runtime = getattr(handle.callbacks, "runtime", None)
    if runtime is None or runtime.turn_watchdog is None:
        return
    runtime.turn_watchdog.note_response()


def request_user_cancel(runtime: SessionRuntime) -> bool:
    watchdog = runtime.turn_watchdog
    if watchdog is None or watchdog.service.runtime(watchdog.session_id) is not runtime:
        return False
    if runtime.closed or not runtime.turn_open:
        return False
    return bool(watchdog.request_cancel())


def cancel_requested(runtime: SessionRuntime) -> bool:
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
