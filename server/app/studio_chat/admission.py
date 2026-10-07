"""Human prompt admission: prepare without side effects, then atomically accept."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from server.app.auth.scoped_tokens import renew_scoped_token
from server.app.auth.sessions import hash_token
from server.app.jobs.queries.studio_chat_admission import StudioChatAdmissionRejected
from server.app.services.job_errors import ConflictError
from server.app.studio_chat import compaction, inbound_queue
from server.app.studio_chat.background_wakeup import prepare_rearm
from server.app.studio_chat.payloads import serialize_message
from server.app.studio_chat.resume_context import prepare_resume_prompt
from server.app.studio_chat.session_orphan import reject_orphaned_session
from server.app.studio_chat.token_admission import require_live_run_token
from server.app.studio_chat.turn_state import open_turn

if TYPE_CHECKING:
    from server.app.studio_chat.service import StudioChatService

logger = logging.getLogger(__name__)


def send_message(
    service: StudioChatService, session_id: str, workspace_id: str, text: str
) -> dict[str, Any]:
    session = service.get_session(session_id, workspace_id)
    if session["status"] == "closed":
        raise ConflictError("Chat session is closed")
    runtime = service.runtime(session_id)
    if runtime is None:
        reject_orphaned_session(service, session_id, session)
    from server.app.studio_chat.prompts import STUDIO_AUTHORING_BOOTSTRAP

    with runtime.lock:
        require_live_run_token(service, session_id, runtime)
        if compaction.send_blocked(service._db, session_id, runtime, text):
            raise ConflictError(compaction.SEND_BLOCKED_DETAIL)
        current = service.get_session(session_id)
        # #882: behind a background turn (or already-queued messages) the
        # message is queued instead of refused (inbound_queue.py).
        queueing = inbound_queue.should_queue(runtime, current["status"])
        if current["status"] != "idle" and not queueing:
            raise ConflictError(f"Chat session is busy ({current['status']})")
        if compaction.late_gate_blocked(service._db, session_id, runtime, text, claimed=False):
            raise ConflictError(compaction.SEND_BLOCKED_DETAIL)
        # All fallible preparation precedes admission; no claim, stream
        # reset, message, or consumed resume marker exists to compensate.
        first_prompt = service._db.count_studio_chat_user_messages(session_id) == 0
        renew_scoped_token(service._db, runtime.token)
        prompt_text = (STUDIO_AUTHORING_BOOTSTRAP + text) if first_prompt else text
        prompt_text, _ = prepare_resume_prompt(
            runtime, service._db, session_id, first_prompt, prompt_text
        )
        require_live_run_token(service, session_id, runtime)
        commit_wakeup = prepare_rearm(runtime)
        if queueing:
            try:
                queued_row = inbound_queue.enqueue(
                    service, session_id, runtime, text, prompt_text, commit_wakeup
                )
            except StudioChatAdmissionRejected:
                require_live_run_token(service, session_id, runtime)
                raise ConflictError("Chat session is no longer running") from None
            if queued_row is None:
                raise ConflictError("Chat session agent is not running")
            _publish(service, session_id, queued_row)
            return queued_row
        message = None

        def accept() -> None:
            nonlocal message
            message = service._db.accept_studio_chat_message(
                session_id, hash_token(runtime.token), text
            )
            # Finish local state before the queue becomes visible: replay
            # filtering can inspect loading before acquiring runtime.lock.
            runtime.resume_transcript_pending = False
            open_turn(runtime, text, message_id=str(message["id"]), prompt=prompt_text)
            commit_wakeup()

        try:
            queued = runtime.handle.send_prompt(prompt_text, accept=accept)
        except StudioChatAdmissionRejected:
            require_live_run_token(service, session_id, runtime)
            raise ConflictError("Chat session is no longer idle") from None
        if not queued:
            service._db.update_studio_chat_session_if(
                session_id, status_in=("idle",), status="error"
            )
            raise ConflictError("Chat session agent is not running")
        assert message is not None
        _publish(service, session_id, message)
    return message


def _publish(service: StudioChatService, session_id: str, message: dict[str, Any]) -> None:
    service.store.publish(session_id, {"type": "message", "message": serialize_message(message)})
    try:
        service.store.publish_session(session_id)
    except Exception:
        # #204 broad-except audit: admission and enqueue succeeded;
        # a snapshot read failure must not turn success into a retry.
        # REST/SSE refill recovers the snapshot; preserve the cause.
        logger.warning("accepted chat snapshot failed for %s", session_id, exc_info=True)
