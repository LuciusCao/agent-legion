"""Bind one direct DB accept to its unique TurnLease."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from server.app.jobs.queries.studio_chat_admission import StudioChatAdmissionRejected
from server.app.studio_chat.admission_recovery import (
    claim_direct_message,
    fail_accepted_turn,
    latest_message_seq,
)
from server.app.studio_chat.turn_state import open_turn
from server.app.studio_chat.turn_watchdog import begin_turn_admission

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
    after_seq = latest_message_seq(service, session_id)
    token = begin_turn_admission(runtime)
    if token is None:
        raise StudioChatAdmissionRejected("Chat session already has an active or fatal turn")
    message = claim_direct_message(service, session_id, runtime, text, after_seq, token)
    try:
        runtime.resume_transcript_pending = False
        open_turn(runtime, text, owner=token, message_id=str(message["id"]), prompt=prompt_text)
        commit_wakeup()
    except Exception as exc:
        # #204 broad-except audit: acceptance is durable; retain M19 identity
        # and use conservative fatal instead of aborting an accepted input.
        fail_accepted_turn(
            runtime, token, exc, text=text, message_id=str(message["id"]), prompt=prompt_text
        )
        raise
    return message, token
