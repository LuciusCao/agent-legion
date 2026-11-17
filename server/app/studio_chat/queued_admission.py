"""Queued human turn claim with attempt-scoped delivery identity."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from server.app.auth.sessions import hash_token
from server.app.jobs.queries.studio_chat_admission import StudioChatAdmissionRejected
from server.app.studio_chat.timing import prompt_timeout_for
from server.app.studio_chat.token_keepalive import _token_alive, invalidate_run_token

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService
    from server.app.studio_chat.turn_lease import TurnLease

DROPPED_BUSY = "会话状态已变化，排队消息未投递，请重发"
DROPPED_TOKEN = "工具通道已失效，排队消息未投递；请点「继续对话」重建后重发"
DELIVERED_DETAIL = "排队消息已开始处理"


def claim_queued_turn(
    service: StudioChatService,
    session_id: str,
    runtime: SessionRuntime,
    message_id: str,
    owner: TurnLease,
    publish: Callable[[StudioChatService, str, dict[str, Any]], None],
) -> str | None:
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
    owner.delivery_seq = int(row["seq"])
    publish(service, session_id, row)
    return None
