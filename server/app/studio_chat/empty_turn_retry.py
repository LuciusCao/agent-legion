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

import logging
from typing import TYPE_CHECKING

from server.app.jobs.queries.studio_chat_admission import StudioChatAdmissionRejected
from server.app.services.job_errors import ConflictError
from server.app.studio_chat import compaction
from server.app.studio_chat.token_admission import require_live_run_token
from server.app.studio_chat.turn_state import open_turn

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService

logger = logging.getLogger(__name__)

RETRY_DETAIL = "已重新投递上一条未被处理的消息"


def retry_empty_turn(service: StudioChatService, session_id: str, runtime: SessionRuntime) -> bool:
    """Re-deliver the armed empty-turn message; False when nothing is armed."""
    with runtime.lock:
        if runtime.empty_turn_retry is None:
            return False
        require_live_run_token(service, session_id, runtime)
        message_id, text, prompt = runtime.empty_turn_retry
        if compaction.send_blocked(service.db, session_id, runtime, text):
            raise ConflictError(compaction.SEND_BLOCKED_DETAIL)

        def accept() -> None:
            if not service.db.claim_studio_chat_turn(session_id):
                raise StudioChatAdmissionRejected
            open_turn(runtime, text, message_id=message_id, prompt=prompt)
            try:
                service.store.append_message(
                    session_id,
                    "status",
                    "system",
                    {"event": "empty_turn_retry", "message_id": message_id, "detail": RETRY_DETAIL},
                )
            except Exception:
                # #204 broad-except audit: the claim and turn are committed;
                # raising here would strand a running row with no prompt.
                # The notice is advisory (the reply itself shows the replay),
                # so log with traceback and continue the hand-off.
                logger.warning("empty turn retry notice failed for %s", session_id, exc_info=True)

        try:
            queued = runtime.handle.send_prompt(prompt, accept=accept)
        except StudioChatAdmissionRejected:
            raise ConflictError("Chat session is no longer idle") from None
        if not queued:
            raise ConflictError("Chat session agent is not running")
    service.store.publish_session(session_id)
    return True
