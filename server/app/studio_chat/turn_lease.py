"""One admission/turn/cancel owner with kill-first, claim-later cleanup (#1153)."""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import TYPE_CHECKING, Any, cast

from server.app.studio_chat.process_exit import report_connection_loss_sync
from server.app.studio_chat.prompt_outcome import PromptWedgedError

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime

logger = logging.getLogger(__name__)
_ACTIVE = ("CLAIMING", "OPEN", "RESPONDED")
_STARTABLE = ("OPEN", "RESPONDED")
_TERMINAL = ("CLOSED", "ABORTED", "KILLING", "FATAL")


class TurnLease:
    def __init__(self, runtime: SessionRuntime) -> None:
        self.runtime = runtime
        self.handle = runtime.handle
        self.previous_owner = runtime.turn_owner
        self.process = self.handle._process
        self.callbacks = cast(Any, self.handle.callbacks)
        self.service = self.callbacks._service
        self.session_id = self.callbacks._session_id
        self.state = "CLAIMING"
        self.requested_at: float | None = None
        self.cancel_epoch = 0
        self.cancel_deadline: float | None = None
        self.total_deadline: float | None = None
        self.deadline: float | None = None
        self.generation = 0
        self.reason = "timeout"
        self.timer: threading.Timer | None = None
        self.cleanup_thread: threading.Thread | None = None
        self.fatal_detail: str | None = None
        self.delivery_seq = 0

    @property
    def _cancel_grace_seconds(self) -> float:
        return self.handle.cancel_grace_seconds

    def activate(self) -> None:
        with self.handle.control_lock:
            if self.state != "CLAIMING":
                return
            self.state = "OPEN"
            self.total_deadline = self.deadline = (
                time.monotonic() + self.handle.prompt_timeout_seconds + self._cancel_grace_seconds
            )
            self._arm_timer_locked()

    def request_cancel(self, requested_at: float) -> bool:
        with self.handle.control_lock:
            if self.state in _TERMINAL:
                return False
            if self.requested_at is None:
                self.requested_at = requested_at
                self.cancel_deadline = requested_at + self._cancel_grace_seconds
            self.cancel_epoch += 1
            self._arm_timer_locked()
            return True

    def note_response(self) -> None:
        with self.handle.control_lock:
            if self.state != "OPEN":
                return
            self.state = "RESPONDED"
            self.total_deadline = None
            self.deadline = self.cancel_deadline
            self._arm_timer_locked()

    def close(self) -> bool:
        with self.handle.control_lock:
            if self.state in _TERMINAL:
                return False
            self.state = "CLOSED"
            self.deadline = self.total_deadline = self.cancel_deadline = None
            self._arm_timer_locked()
            return True

    def abort(self) -> None:
        with self.handle.control_lock:
            if self.state != "CLAIMING":
                return
            self.state = "ABORTED"
            self.deadline = self.total_deadline = self.cancel_deadline = None
            if self.handle.active_lease is self and self.runtime.turn_owner is self:
                self.runtime.turn_owner = self.previous_owner
            self._arm_timer_locked()

    def can_start(self) -> bool:
        with self.handle.control_lock:
            return self._can_start_locked()

    def _can_start_locked(self) -> bool:
        return (
            self.handle.active_lease is self
            and self.state in _STARTABLE
            and self.handle.fatal_lease is None
            and not self.handle._closed
            and not self.handle._stop_requested
        )

    def _arm_timer_locked(self) -> None:
        self.generation += 1
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        if self.state in _TERMINAL:
            return
        deadlines = [value for value in (self.total_deadline, self.cancel_deadline) if value]
        if not deadlines:
            return
        self.deadline = min(deadlines)
        self.reason = (
            "cancel"
            if self.cancel_deadline is not None and self.deadline == self.cancel_deadline
            else "timeout"
        )
        generation, reason, cancel_epoch = self.generation, self.reason, self.cancel_epoch
        self.timer = threading.Timer(
            max(0, self.deadline - time.monotonic()),
            self._fire,
            args=(generation, reason, cancel_epoch),
        )
        self.timer.name = "studio-turn-lease"
        self.timer.daemon = True
        self.timer.start()

    def fatal_for_prompt(self) -> bool:
        with self.handle.control_lock:
            return self.state in ("KILLING", "FATAL")

    def fail_claim_uncertain(self, detail: str) -> None:
        with self.handle.control_lock:
            if self.state not in _ACTIVE or not self._fatal_identity_locked():
                return
            self._install_killing_locked(detail)
        self._kill_pinned_process()
        self._start_cleanup("claim")

    def _fatal_identity_locked(self) -> bool:
        return (
            self.handle.active_lease is self
            and self.handle.fatal_lease is None
            and self.callbacks.runtime is self.runtime
            and self.runtime.handle is self.handle
            and self.handle._process is self.process
            and not self.handle._closed
            and self.runtime.turn_owner is self
        )

    def _install_killing_locked(self, detail: str) -> None:
        self.state = "KILLING"
        self.handle.fatal_lease = self
        if self.handle._transport_failure is None and self.fatal_detail is None:
            self.fatal_detail = detail

    def _fire(self, generation: int, reason: str, cancel_epoch: int) -> None:
        with self.handle.control_lock:
            if (
                self.state in _TERMINAL
                or generation != self.generation
                or reason != self.reason
                or self.deadline is None
            ):
                return
            if time.monotonic() < self.deadline:
                self._arm_timer_locked()
                return
            if not self._fatal_identity_locked() or (
                reason == "cancel" and cancel_epoch != self.cancel_epoch
            ):
                return
            suffix = "user session/cancel" if reason == "cancel" else "session/cancel"
            detail = str(
                PromptWedgedError(
                    f"prompt turn wedged: no response within {self._cancel_grace_seconds}s "
                    f"after {suffix}"
                )
            )
            self._install_killing_locked(detail)
        self._kill_pinned_process()
        self._start_cleanup(reason)

    def _kill_pinned_process(self) -> None:
        if self.handle._process is not self.process or self.process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            self.process.kill()

    def _start_cleanup(self, reason: str) -> None:
        self.cleanup_thread = threading.Thread(
            target=self._cleanup_after_start,
            args=(reason,),
            name="studio-turn-lease-cleanup",
            daemon=True,
        )
        self.cleanup_thread.start()

    def _cleanup_after_start(self, reason: str) -> None:
        with self.handle.rpc_mutex, self.handle.control_lock:
            if self.state != "KILLING" or self.handle.active_lease is not self:
                return
            if self.handle._process is not self.process:
                return
            self.state = "FATAL"
            if self.handle._transport_failure is None:
                self.handle._transport_failure = self.fatal_detail
            self.handle._stop_requested = True
        if self.service.runtime(self.session_id) is not self.runtime:
            self.service.teardown_runtime(self.session_id, self.runtime, expected=self.runtime)
            return
        self._escalate(reason)

    def _escalate(self, reason: str) -> None:
        detail = self.handle._transport_failure or self.fatal_detail or "prompt turn wedged"
        try:
            if reason == "timeout":
                _report_callback("timeout settlement", self.callbacks.on_turn_timeout)
            _report_callback("fatal error", self.callbacks.on_error, detail)
            report_connection_loss_sync(self.callbacks, detail)
        finally:
            try:
                self.service.teardown_runtime(self.session_id, self.runtime, expected=self.runtime)
            except Exception:
                # #204 broad-except audit: retain cleanup failures with traceback;
                # M19 on_exit remains an idempotent peer and never targets a successor.
                logger.warning("studio lease cleanup failed for %s", self.session_id, exc_info=True)


def _report_callback(label: str, callback: Any, *args: Any) -> None:
    try:
        callback(*args)
    except Exception:
        # #204 broad-except audit: projection failures cannot skip the shared exit
        # settlement and direct-child cleanup. Preserve each traceback for retry.
        logger.warning("studio lease %s callback failed", label, exc_info=True)


def begin_turn_admission(runtime: SessionRuntime) -> TurnLease | None:
    with runtime.handle.control_lock:
        active = runtime.handle.active_lease
        if active is not None and active.state not in ("CLOSED", "ABORTED"):
            return None
        if runtime.handle._closed or runtime.handle._stop_requested or runtime.handle.fatal_lease:
            return None
        lease = TurnLease(runtime)
        runtime.handle.active_lease = lease
        runtime.turn_owner = lease
        return lease


def cancel_turn_admission(lease: TurnLease | None) -> None:
    if lease is not None:
        lease.abort()


def activate_turn_lease(owner: object | None) -> object | None:
    if isinstance(owner, TurnLease):
        owner.activate()
    return owner


def request_user_cancel(runtime: SessionRuntime, requested_at: float) -> bool:
    with runtime.handle.control_lock:
        lease = runtime.handle.active_lease
    return isinstance(lease, TurnLease) and lease.request_cancel(requested_at)


def cancel_handle(runtime: SessionRuntime, requested_at: float) -> None:
    request_user_cancel(runtime, requested_at)
    runtime.handle.cancel()


def cancel_requested(runtime: SessionRuntime) -> bool:
    lease = getattr(runtime.handle, "active_lease", None)
    if not isinstance(lease, TurnLease):
        return False
    with runtime.handle.control_lock:
        return runtime.handle.active_lease is lease and lease.requested_at is not None


def note_prompt_response(handle: Any, owner: object | None = None) -> None:
    if isinstance(owner, TurnLease):
        owner.note_response()
        return
    lease = getattr(handle, "active_lease", None)
    if not isinstance(lease, TurnLease):
        return
    with handle.control_lock:
        current = handle.active_lease
    if isinstance(current, TurnLease):
        current.note_response()


def disarm_turn_watchdog(runtime: SessionRuntime, owner: object | None = None) -> bool:
    lease = runtime.turn_owner
    if not isinstance(lease, TurnLease):
        return True
    if owner is not None and lease is not owner:
        return False
    with runtime.handle.control_lock:
        if runtime.turn_owner is not lease:
            return False
    return lease.state == "CLOSED" or lease.close()


def transport_failed(handle: Any) -> bool:
    if getattr(handle, "fatal_lease", None) is not None:
        return True
    return isinstance(getattr(handle, "transport_failure", lambda: None)(), str)
