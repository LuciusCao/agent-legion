"""Idle-state 「继续对话」 for a confirmed empty turn (#882, #863 follow-up).

A turn the empty_turn verdict confirmed (zero content after the grace) most
likely never reached the agent, yet the session is idle — the closed/error
resume path does not apply. The verdict arms ``runtime.empty_turn_retry``
with the human message behind that turn; the resume endpoint, called on a
live idle session, re-delivers exactly that prompt as a new turn.

Duplicate-delivery guard: the slot is a single in-memory value consumed
inside the turn claim (and cleared by any newer ``open_turn``), so a
double click, a second tab, or a click after the user already sent
something new finds nothing to replay. No new user row is written — the
original message stays the only bubble; a status row records the replay.

Automatic re-delivery is deliberately not done: the platform cannot tell a
prompt the ACP layer dropped from a legitimately silent turn, and an
automatic replay could hit the same quiescence window again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.auth.scoped_tokens import renew_scoped_token
from server.app.auth.sessions import hash_token
from server.app.jobs.queries.studio_chat_admission import StudioChatAdmissionRejected
from server.app.services.job_errors import ConflictError
from server.app.studio_chat import compaction
from server.app.studio_chat.inbound_queue import publish_committed
from server.app.studio_chat.token_admission import require_live_run_token
from server.app.studio_chat.turn_state import open_turn
from server.app.studio_chat.unprompted_queue import holding, refresh

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService

RETRY_DETAIL = "已重新投递上一条未被处理的消息"
RETRY_HELD_DETAIL = "agent 正在自发处理后台结果，请在其结束后再点「继续对话」"


def retry_empty_turn(service: StudioChatService, session_id: str, runtime: SessionRuntime) -> bool:
    """Re-deliver the armed empty-turn message; False when nothing is armed."""
    # #1029: observe a just-started Kimi Code unprompted turn before deciding,
    # as admission.send_message does (step lock → runtime.lock order).
    refresh(runtime)
    with runtime.lock:
        if runtime.empty_turn_retry is None:
            return False
        require_live_run_token(service, session_id, runtime)
        if holding(runtime):
            # #1029: a replay into a Kimi Code unprompted turn is lost again.
            raise ConflictError(RETRY_HELD_DETAIL)
        message_id, text, prompt = runtime.empty_turn_retry
        if compaction.send_blocked(service.db, session_id, runtime, text):
            raise ConflictError(compaction.SEND_BLOCKED_DETAIL)
        # Same credential path as human admission (admission.send_message):
        # slide the run token before the turn, then re-check it under lock
        # in the claim transaction itself.
        renew_scoped_token(service.db, runtime.token)
        require_live_run_token(service, session_id, runtime)

        notice = {"event": "empty_turn_retry", "message_id": message_id, "detail": RETRY_DETAIL}
        committed: dict[str, Any] = {}

        def accept() -> None:
            # Claim + replay notice commit together (no claimed turn without
            # its timeline record, and no record without the turn).
            committed.update(
                service.db.claim_studio_chat_turn_with_token(
                    session_id, hash_token(runtime.token), notice
                )
            )
            open_turn(runtime, text, message_id=message_id, prompt=prompt)

        try:
            queued = runtime.handle.send_prompt(prompt, accept=accept)
        except StudioChatAdmissionRejected:
            require_live_run_token(service, session_id, runtime)
            raise ConflictError("Chat session is no longer idle") from None
        if not queued:
            raise ConflictError("Chat session agent is not running")
    publish_committed(service, session_id, committed)
    return True
