"""Bind one direct DB accept to its unique TurnLease."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from server.app.auth.sessions import hash_token
from server.app.jobs.queries.studio_chat_admission import StudioChatAdmissionRejected
from server.app.studio_chat.turn_state import open_turn
from server.app.studio_chat.turn_watchdog import begin_turn_admission, cancel_turn_admission

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService
    from server.app.studio_chat.turn_lease import TurnLease


def accept_direct_turn(
    service: StudioChatService,
    session_id: str,
    runtime: SessionRuntime,
    text: str,
    prompt_text: str,
    commit_wakeup: Callable[[], None],
) -> tuple[dict[str, Any], TurnLease]:
    token = begin_turn_admission(runtime)
    if token is None:
        raise StudioChatAdmissionRejected("Chat session already has an active or fatal turn")
    try:
        message = service._db.accept_studio_chat_message(
            session_id, hash_token(runtime.token), text
        )
        runtime.resume_transcript_pending = False
        open_turn(runtime, text, owner=token, message_id=str(message["id"]), prompt=prompt_text)
        commit_wakeup()
        return message, token
    except Exception:
        # #204 broad-except audit: only uncommitted admission/cancel identity is
        # cleared; the original failure re-raises and durable proof is not rolled back.
        cancel_turn_admission(token)
        raise
