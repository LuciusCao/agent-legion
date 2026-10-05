"""Inbound queue for human messages that arrive during a background turn (#882).

A background wakeup (background_delivery.wake_session) claims the session
like a human turn does, so a message sent while it runs used to be refused
with 409 — and a client that raced the claim (it still saw ``idle``) lost
the text it had already cleared from the composer (#863 comment ④).

Now, while a platform-initiated turn holds the session, the message is
persisted immediately (``content.queued``) and handed to the ACP prompt
queue behind the running turn. The handle's prompt loop is strictly FIFO
and only dequeues after the previous turn settled (on_turn_end has moved
the row back to idle), so delivery order is the arrival order. At dequeue
time ``before_start`` claims the turn exactly like human admission and
records ``queued_delivered``; if the session can no longer take it
(compaction window, dead run token, row not idle) the prompt is not sent
and ``queued_dropped`` tells the user to resend — never a silent loss.

While any message is queued, later sends queue too (FIFO — a direct claim
would overtake them) and background wakeups stand back (wake_session), so
nothing can slip in between a turn's end and the queued message's start.
A runtime torn down with messages still queued never starts them; the UI
marks such rows undelivered once the session is no longer live.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from server.app.auth.sessions import hash_token
from server.app.jobs.queries.studio_chat_admission import StudioChatAdmissionRejected
from server.app.studio_chat import compaction
from server.app.studio_chat.token_keepalive import _token_alive, invalidate_run_token
from server.app.studio_chat.turn_state import open_turn

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService

logger = logging.getLogger(__name__)

DELIVERED_DETAIL = "排队消息已开始处理"
DROPPED_COMPACTING = "上下文压缩中，排队消息未投递，请稍后重发"
DROPPED_TOKEN = "工具通道已失效，排队消息未投递；请点「继续对话」重建后重发"
DROPPED_BUSY = "会话状态已变化，排队消息未投递，请重发"
DROPPED_ERROR = "排队消息投递失败，请重发"


def should_queue(runtime: SessionRuntime, status: str) -> bool:
    """Caller holds runtime.lock: queue behind a background turn, or behind
    messages already queued (keeps FIFO across the turn boundary)."""
    if runtime.inbound_pending > 0:
        return status in ("idle", "running", "awaiting_permission")
    return (
        status in ("running", "awaiting_permission")
        and runtime.turn_open
        and runtime.turn_background
    )


def enqueue(
    service: StudioChatService,
    session_id: str,
    runtime: SessionRuntime,
    text: str,
    prompt: str,
    commit_wakeup: Callable[[], None],
) -> dict[str, Any] | None:
    """Caller holds runtime.lock with all prompt preparation done; returns the
    persisted user row, or None when the handle no longer accepts input."""
    message: dict[str, Any] | None = None

    def accept() -> None:
        nonlocal message
        message = service.db.enqueue_studio_chat_message(
            session_id, hash_token(runtime.token), text
        )
        runtime.inbound_pending += 1
        runtime.resume_transcript_pending = False
        commit_wakeup()

    def before_start() -> bool:
        assert message is not None
        return _deliver(service, session_id, runtime, str(message["id"]), text, prompt)

    if not runtime.handle.send_prompt(prompt, accept=accept, before_start=before_start):
        return None
    return message


def _deliver(
    service: StudioChatService,
    session_id: str,
    runtime: SessionRuntime,
    message_id: str,
    text: str,
    prompt: str,
) -> bool:
    """ACP thread, at the queued prompt's turn: claim like human admission."""
    with runtime.lock:
        runtime.inbound_pending -= 1
        if runtime.closed or service.runtime(session_id) is not runtime:
            return False
        detail: str | None = None
        try:
            if compaction.send_blocked(service.db, session_id, runtime, text):
                detail = DROPPED_COMPACTING
            elif not _token_alive(service, runtime.token):
                invalidate_run_token(service, session_id, runtime)
                detail = DROPPED_TOKEN
            elif (detail := _claim(service, session_id, runtime)) is None:
                open_turn(runtime, text, message_id=message_id, prompt=prompt)
        except Exception:
            # #204 broad-except audit: every step before the claim is a
            # read/notice and the claim is the last fallible call, so a raise
            # means no turn was taken; dropping (with a visible notice) beats
            # killing the prompt loop. Traceback retained.
            logger.warning("queued chat message delivery failed for %s", session_id, exc_info=True)
            detail = DROPPED_ERROR
        _note(service, session_id, message_id, detail)
        return detail is None


def _claim(service: StudioChatService, session_id: str, runtime: SessionRuntime) -> str | None:
    """Token lock + idle claim in one transaction (human-admission parity)."""
    try:
        service.db.claim_studio_chat_turn_with_token(session_id, hash_token(runtime.token))
    except StudioChatAdmissionRejected:
        alive = _token_alive(service, runtime.token)
        if not alive:
            invalidate_run_token(service, session_id, runtime)
        return DROPPED_BUSY if alive else DROPPED_TOKEN
    return None


def _note(service: StudioChatService, session_id: str, message_id: str, detail: str | None) -> None:
    event = "queued_delivered" if detail is None else "queued_dropped"
    try:
        service.store.append_message(
            session_id,
            "status",
            "system",
            {"event": event, "message_id": message_id, "detail": detail or DELIVERED_DETAIL},
        )
        service.store.publish_session(session_id)
    except Exception:
        # #204 broad-except audit: advisory notice after the outcome is
        # decided; a raise here would abort a claimed turn's prompt. The UI
        # falls back to the session status; traceback retained.
        logger.warning("queued chat message notice failed for %s", session_id, exc_info=True)
