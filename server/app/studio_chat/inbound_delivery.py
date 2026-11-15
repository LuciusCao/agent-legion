"""Actual before_start delivery and durable evidence for queued human input."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from server.app.auth.sessions import hash_token
from server.app.jobs.queries.studio_chat_admission import StudioChatAdmissionRejected
from server.app.studio_chat import compaction
from server.app.studio_chat.payloads import serialize_message
from server.app.studio_chat.timing import prompt_timeout_for
from server.app.studio_chat.token_keepalive import _token_alive, invalidate_run_token
from server.app.studio_chat.turn_state import open_turn
from server.app.studio_chat.turn_watchdog import transport_failed

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService

logger = logging.getLogger(__name__)

DELIVERED_DETAIL = "排队消息已开始处理"
DROPPED_COMPACTING = "上下文压缩中，排队消息未投递，请稍后重发"
DROPPED_TOKEN = "工具通道已失效，排队消息未投递；请点「继续对话」重建后重发"
DROPPED_BUSY = "会话状态已变化，排队消息未投递，请重发"
DROPPED_ERROR = "排队消息投递失败，请重发"


def note_enqueued(runtime: SessionRuntime, message: dict[str, Any]) -> None:
    runtime.inbound_pending += 1
    runtime.inbound_messages[str(message["id"])] = "queued"


def hold(
    service: StudioChatService,
    session_id: str,
    runtime: SessionRuntime,
    text: str,
    prompt: str,
    commit_wakeup: Callable[[], None],
) -> dict[str, Any] | None:
    """Persist a human message behind an open unprompted-turn gate."""
    gate = runtime.unprompted_gate
    if gate is None or runtime.closed:
        return None
    message = service.db.enqueue_studio_chat_message(session_id, hash_token(runtime.token), text)
    note_enqueued(runtime, message)
    runtime.resume_transcript_pending = False
    commit_wakeup()
    gate.held.append((str(message["id"]), text, prompt))
    return message


def _deliver(
    service: StudioChatService,
    session_id: str,
    runtime: SessionRuntime,
    message_id: str,
    text: str,
    prompt: str,
) -> bool:
    """Claim at the actual ACP start boundary, on a worker thread."""
    if (gate := runtime.unprompted_gate) is not None and gate.defer(message_id, text, prompt):
        return False
    with runtime.lock:
        failed = transport_failed(runtime.handle)
        if runtime.closed or failed or service.runtime(session_id) is not runtime:
            if not runtime.exit_started and not failed:
                runtime.inbound_pending = max(0, runtime.inbound_pending - 1)
            return False
        runtime.inbound_pending -= 1
        detail: str | None = None
        try:
            if compaction.send_blocked(service.db, session_id, runtime, text):
                detail = DROPPED_COMPACTING
            elif not _token_alive(service, runtime.token, prompt_timeout_for(runtime)):
                invalidate_run_token(service, session_id, runtime)
                detail = DROPPED_TOKEN
            elif (detail := _claim(service, session_id, runtime, message_id)) is None:
                open_turn(runtime, text, message_id=message_id, prompt=prompt)
        except Exception:
            # #204 broad-except audit: a claim can commit and still surface a
            # transport/DB exception. Read its durable proof before deciding:
            # only a genuinely unclaimed message may be called undelivered.
            logger.warning("queued chat message delivery failed for %s", session_id, exc_info=True)
            if _has_delivery_proof(service, session_id, message_id):
                open_turn(runtime, text, message_id=message_id, prompt=prompt)
                return True
            detail = DROPPED_ERROR
        if detail is not None:
            _note_dropped(service, session_id, message_id, detail, runtime=runtime)
        return detail is None


def _has_delivery_proof(service: StudioChatService, session_id: str, message_id: str) -> bool:
    return any(
        message.get("kind") == "status"
        and (content := message.get("content") or {}).get("event") == "queued_delivered"
        and content.get("message_id") == message_id
        for message in service.db.list_studio_chat_messages(session_id)
    )


def _claim(
    service: StudioChatService, session_id: str, runtime: SessionRuntime, message_id: str
) -> str | None:
    """Commit token lock, idle claim and delivery proof in one transaction."""
    notice = {"event": "queued_delivered", "message_id": message_id, "detail": DELIVERED_DETAIL}
    try:
        row = service.db.claim_studio_chat_turn_with_token(
            session_id, hash_token(runtime.token), notice
        )
    except StudioChatAdmissionRejected:
        alive = _token_alive(service, runtime.token, prompt_timeout_for(runtime))
        if not alive:
            invalidate_run_token(service, session_id, runtime)
        return DROPPED_BUSY if alive else DROPPED_TOKEN
    publish_committed(service, session_id, row)
    return None


def publish_committed(service: StudioChatService, session_id: str, row: dict[str, Any]) -> None:
    """Push an already-committed status row + snapshot; best-effort only."""
    try:
        service.store.publish(session_id, {"type": "message", "message": serialize_message(row)})
        service.store.publish_session(session_id)
    except Exception:
        # #204 broad-except audit: the row and claim are durable; a failed
        # publish must not abort the claimed turn's prompt. Traceback retained.
        logger.warning("queued chat message publish failed for %s", session_id, exc_info=True)


def _note_dropped(
    service: StudioChatService,
    session_id: str,
    message_id: str,
    detail: str,
    *,
    runtime: SessionRuntime | None = None,
) -> None:
    try:
        service.store.append_message(
            session_id,
            "status",
            "system",
            {"event": "queued_dropped", "message_id": message_id, "detail": detail},
        )
        service.store.publish_session(session_id)
    except Exception:
        # #204 broad-except audit: the drop was decided without an ACP start;
        # a missing advisory row keeps the UI session-status fallback. Traceback retained.
        logger.warning("queued chat message notice failed for %s", session_id, exc_info=True)
    if runtime is not None:
        runtime.inbound_messages.pop(message_id, None)
