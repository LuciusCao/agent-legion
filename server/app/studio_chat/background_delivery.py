"""Fail-closed admission and cancellation-aware ACP delivery for automatic turns."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from server.app.studio_chat.background_admission import (
    background_prompt,
    claim_background_turn,
    open_background_turn,
)
from server.app.studio_chat.timing import prompt_timeout_for
from server.app.studio_chat.token_keepalive import _token_alive, invalidate_run_token
from server.app.studio_chat.turn_state import open_turn
from server.app.studio_chat.turn_watchdog import (
    cancel_requested,
    disarm_turn_watchdog,
    transport_failed,
)
from server.app.studio_chat.unprompted_queue import DEQUEUE_STEP_TIMEOUT_SECONDS, holding, refresh

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService
    from server.app.studio_chat.turn_lease import TurnLease

logger = logging.getLogger(__name__)


def _publish(service: StudioChatService, session_id: str) -> None:
    try:
        service.store.publish_session(session_id)
    except Exception:
        # #204 broad-except audit: a failed snapshot must never replay accepted
        # work or undo a release; REST refills durable state. Preserve traceback.
        logger.warning("Kimi wakeup snapshot failed for %s", session_id, exc_info=True)


def wake_session(
    service: StudioChatService, session_id: str, runtime: SessionRuntime, task_ids: list[str]
) -> bool:
    """Accept one followup, with a second guard at the actual ACP prompt boundary."""
    with runtime.lock:
        if (
            transport_failed(runtime.handle)
            or cancel_requested(runtime)
            or service.runtime(session_id) is not runtime
            or runtime.closed
            or not runtime.background_wakeup_enabled
            or runtime.compacting
            or runtime.turn_open
            # #882: queued human messages go first (inbound_queue.py).
            or runtime.inbound_pending
            # #1029: never prompt into a Kimi Code unprompted turn.
            or holding(runtime)
        ):
            return False
        if not _token_alive(service, runtime.token, prompt_timeout_for(runtime)):
            invalidate_run_token(service, session_id, runtime)
            return False
        owner = claim_background_turn(service, session_id, runtime)
        if owner is None:
            return False
        epoch = runtime.background_epoch

        def release(*, error: bool = False) -> bool:
            # Never stamp a replacement runtime's durable row or turn flags.
            try:
                with service._runtimes_lock:
                    if service._runtimes.get(session_id) is runtime and runtime.turn_owner is owner:
                        runtime.turn_open = False
                        disarm_turn_watchdog(runtime, owner)
                        if (
                            not error
                            and runtime.background_epoch == epoch
                            and runtime.background_wakeup_enabled
                            and runtime.background_cursor is not None
                        ):
                            runtime.background_cursor.pending.update(task_ids)
                        service.db.update_studio_chat_session_if(
                            session_id, status_in=("running",), status="error" if error else "idle"
                        )
            except Exception:
                # #204 broad-except audit: failed claim cleanup must not escape
                # into generic ACP on_turn_error, which lacks this turn's owner.
                # The watcher retries the same guarded cleanup; retain traceback.
                runtime.background_cleanup = lambda: release(error=error)
                logger.warning("Kimi wakeup claim cleanup failed for %s", session_id, exc_info=True)
                return False
            _publish(service, session_id)
            return True

        def before_start() -> TurnLease | bool:
            # #1109: the claim above read the last poll (our caller holds
            # runtime.lock, so it cannot step); observe the journal here, at
            # the send boundary, on the worker thread with no lock held.
            observed = refresh(runtime, DEQUEUE_STEP_TIMEOUT_SECONDS)
            with runtime.lock:
                try:
                    valid = (
                        not transport_failed(runtime.handle)
                        and not cancel_requested(runtime)
                        and service.runtime(session_id) is runtime
                        and runtime.turn_owner is owner
                        and not runtime.closed
                        and runtime.background_wakeup_enabled
                        and runtime.background_epoch == epoch
                        and not runtime.compacting
                    )
                    if valid and (holding(runtime) or not observed):
                        # Stand back without burning the token: release()
                        # re-arms the task ids and the cursor retries later.
                        release()
                        return False
                    if valid and _token_alive(service, runtime.token, prompt_timeout_for(runtime)):
                        return owner
                    release()
                    if valid:
                        invalidate_run_token(service, session_id, runtime)
                    return False
                except Exception:
                    # #204 broad-except audit: no ACP prompt has started; release
                    # our claim and expose retryable state, retaining the traceback.
                    release()
                    logger.warning("Kimi queued wakeup rejected for %s", session_id, exc_info=True)
                    return False

        try:
            open_background_turn(runtime, owner, open_turn)
            prompt = background_prompt(task_ids)
            if service.runtime(session_id) is not runtime:
                runtime.turn_open = False
                disarm_turn_watchdog(runtime, owner)
                return False
            if not runtime.handle.send_prompt(prompt, lease=owner, before_start=before_start):
                release(error=True)
                return False
        except Exception:
            # #204 broad-except audit: ordinary enqueue failure releases only a
            # nonfatal claim; accepted open uncertainty retains its fatal owner.
            if not owner.fatal_for_prompt():
                release()
            raise
        _publish(service, session_id)
        return True
