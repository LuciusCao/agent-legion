"""Off-loop bound for user cancellation and wedged prompt turns (#1153 A2)."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from server.app.studio_chat.process_exit import report_connection_loss_sync
from server.app.studio_chat.prompt_outcome import PromptWedgedError

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime

logger = logging.getLogger(__name__)


class TurnWatchdog:
    """One off-loop deadline owner for one runtime turn and direct child."""

    def __init__(self, runtime: SessionRuntime, owner: object, process: Any) -> None:
        self.runtime = runtime
        self.owner = owner
        self.process = process
        self.handle = runtime.handle
        callbacks: Any = self.handle.callbacks
        self.callbacks = callbacks
        self.service = callbacks._service
        self.session_id = callbacks._session_id
        self._timer_lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._generation = 0
        self._cancel_epoch = 0
        self._total_deadline = (
            time.monotonic() + self.handle.prompt_timeout_seconds + self.handle.cancel_grace_seconds
        )
        self._cancel_deadline: float | None = None
        self._deadline = self._total_deadline
        self._reason = "timeout"
        self._disarmed = self._claimed = False

    def arm(self) -> None:
        self.runtime.turn_watchdog = self
        with self._timer_lock:
            self._schedule_locked()

    def request_cancel(self) -> bool:
        """Caller holds runtime.lock; the first cancel deadline never slides forward."""
        with self._timer_lock:
            if self._disarmed or self._claimed:
                return False
            self._cancel_epoch += 1
            requested = time.monotonic() + self.handle.cancel_grace_seconds
            self._cancel_deadline = (
                requested
                if self._cancel_deadline is None
                else min(self._cancel_deadline, requested)
            )
            if self._cancel_deadline < self._deadline:
                self._deadline = self._cancel_deadline
                self._reason = "cancel"
            self._schedule_locked()
            return True

    def disarm(self, owner: object | None = None) -> None:
        """Safe with or without runtime.lock; never waits for the ACP loop."""
        if owner is not None and owner is not self.owner:
            return
        with self._timer_lock:
            self._disarmed = True
            self._generation += 1
            if self._timer is not None:
                self._timer.cancel()

    def note_response(self) -> None:
        """A returned prompt no longer needs the total-timeout backstop."""
        with self._timer_lock:
            if self._reason == "timeout":
                self._disarmed = True
                self._generation += 1
                if self._timer is not None:
                    self._timer.cancel()

    def _schedule_locked(self) -> None:
        self._generation += 1
        generation, reason, cancel_epoch = self._generation, self._reason, self._cancel_epoch
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(
            max(0, self._deadline - time.monotonic()),
            self._fire,
            args=(generation, reason, cancel_epoch),
        )
        self._timer.name = "studio-turn-watchdog"
        self._timer.daemon = True
        self._timer.start()

    def _fire(self, generation: int, reason: str, cancel_epoch: int) -> None:
        with self.runtime.lock, self._timer_lock:
            if (
                self._disarmed
                or self._claimed
                or generation != self._generation
                or reason != self._reason
            ):
                return
            if time.monotonic() < self._deadline:
                self._schedule_locked()
                return
            with self.handle._state_lock:
                identity_live = not self.handle._closed and self.handle._process is self.process
            if (
                self.runtime.turn_watchdog is not self
                or self.runtime.closed
                or not self.runtime.turn_open
                or self.runtime.turn_owner is not self.owner
                or self.runtime.handle is not self.handle
                or self.service.runtime(self.session_id) is not self.runtime
                or not identity_live
                or (reason == "cancel" and cancel_epoch != self._cancel_epoch)
            ):
                return
            detail = self.handle.transport_failure()
            fatal = detail is None
            if fatal:
                grace = self.handle.cancel_grace_seconds
                suffix = "user session/cancel" if reason == "cancel" else "session/cancel"
                detail = str(
                    PromptWedgedError(
                        f"prompt turn wedged: no response within {grace}s after {suffix}"
                    )
                )
                # Fence this generation before releasing runtime.lock: no next
                # turn can be admitted between the identity check and cleanup.
                if not self.handle._note_connection_lost(detail):
                    return
            self._claimed = True
        self._escalate(reason, detail, fatal)

    def _escalate(self, reason: str, detail: str, fatal: bool) -> None:
        try:
            if fatal:
                if reason == "timeout":
                    _report_callback("timeout settlement", self.callbacks.on_turn_timeout)
                _report_callback("fatal error", self.callbacks.on_error, detail)
            report_connection_loss_sync(self.callbacks, detail)
        finally:
            try:
                self.service.teardown_runtime(self.session_id, self.runtime, expected=self.runtime)
            except Exception:
                # #204 broad-except audit: a daemon watchdog must retain cleanup
                # failures with a traceback; the pinned thread-final on_exit remains
                # an idempotent retry and no successor is ever torn down here.
                logger.warning(
                    "studio watchdog teardown failed for %s", self.session_id, exc_info=True
                )


def _report_callback(label: str, callback: Any, *args: Any) -> None:
    try:
        callback(*args)
    except Exception:
        # #204 broad-except audit: DB/projection failures cannot skip the shared
        # exit settlement and direct-child cleanup. The next idempotent stage runs.
        logger.warning("studio watchdog %s callback failed", label, exc_info=True)


def arm_turn_watchdog(runtime: SessionRuntime) -> None:
    """Caller holds runtime.lock after open_turn has stamped its owner."""
    previous = runtime.turn_watchdog
    if previous is not None:
        previous.disarm()
    runtime.turn_watchdog = None
    handle = runtime.handle
    callbacks = getattr(handle, "callbacks", None)
    if getattr(callbacks, "runtime", None) is not runtime:
        return
    # send_prompt calls accept() while holding _state_lock; do not re-enter it.
    process = handle._process
    if process is not None:
        TurnWatchdog(runtime, runtime.turn_owner, process).arm()


def note_prompt_response(handle: Any) -> None:
    runtime = getattr(handle.callbacks, "runtime", None)
    watchdog = getattr(runtime, "turn_watchdog", None)
    if watchdog is not None:
        watchdog.note_response()


def request_user_cancel(runtime: SessionRuntime) -> bool:
    watchdog = runtime.turn_watchdog
    return watchdog is not None and watchdog.request_cancel()


def disarm_turn_watchdog(runtime: SessionRuntime, owner: object | None = None) -> None:
    watchdog = runtime.turn_watchdog
    if watchdog is not None:
        watchdog.disarm(owner)
