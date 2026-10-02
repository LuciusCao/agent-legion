"""Fail-closed admission and cancellation-aware ACP delivery for automatic turns."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from server.app.studio_chat.token_keepalive import _token_alive, invalidate_run_token
from server.app.studio_chat.turn_state import open_turn

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService

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
            service.runtime(session_id) is not runtime
            or runtime.closed
            or not runtime.background_wakeup_enabled
            or runtime.compacting
            or runtime.turn_open
        ):
            return False
        if not _token_alive(service, runtime.token):
            invalidate_run_token(service, session_id, runtime)
            return False
        if not service.db.claim_studio_chat_turn(session_id):
            return False
        epoch = runtime.background_epoch
        owner = object()

        def release(*, error: bool = False) -> bool:
            # Never stamp a replacement runtime's durable row or turn flags.
            try:
                with service._runtimes_lock:
                    if service._runtimes.get(session_id) is runtime and runtime.turn_owner is owner:
                        runtime.turn_open = False
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

        def before_start() -> bool:
            with runtime.lock:
                try:
                    valid = (
                        service.runtime(session_id) is runtime
                        and runtime.turn_owner is owner
                        and not runtime.closed
                        and runtime.background_wakeup_enabled
                        and runtime.background_epoch == epoch
                        and not runtime.compacting
                    )
                    if valid and _token_alive(service, runtime.token):
                        return True
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
            open_turn(runtime, "", owner=owner)
            prompt = (
                "[Studio 系统通知] 你先前派发的后台子代理任务已到终态："
                + ", ".join(task_ids)
                + "。请处理原生 completion notification，必要时用 TaskOutput 核对结果，"
                "完成验收并向用户汇报；不要重复派发这些任务。"
            )
            if service.runtime(session_id) is not runtime:
                runtime.turn_open = False
                return False
            if not runtime.handle.send_prompt(prompt, before_start=before_start):
                release(error=True)
                return False
        except Exception:
            # #204 broad-except audit: enqueue failed before ownership transfer;
            # release this runtime's claim and let watcher retry with traceback.
            release()
            raise
        _publish(service, session_id)
        return True
