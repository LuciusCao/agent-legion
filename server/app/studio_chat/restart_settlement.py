"""Persistent-evidence settlement after an ungraceful backend restart (#1153).

Unlike same-generation exit_settlement.py, no runtime state survives here. The
message timeline is replayed to reconstruct each human input's delivery and
terminal state. Missing terminals are added idempotently before the session row
is marked resumable; uncertain completion always fails closed to unknown.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.app.studio_chat.service import StudioChatService

RESTART_ERROR_DETAIL = "backend restarted before the session ended"
RESTART_DROPPED = "后端重启前，排队消息未投递，请重发"
RESTART_UNKNOWN = "后端重启前消息已开始处理，处理结果未知；请核对后手动继续，勿直接重发"
_LIVE = ("starting", "idle", "running", "awaiting_permission", "error")


def _timeline(service: StudioChatService, session_id: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    cursor = 0
    while True:
        page = service.db.list_studio_chat_messages_forward(session_id, after_seq=cursor, limit=500)
        messages.extend(page)
        if len(page) < 500:
            return messages
        cursor = int(page[-1]["seq"])


def _reconstruct(messages: list[dict[str, Any]]) -> tuple[dict[str, str], set[str]]:
    states: dict[str, str] = {}
    resolved_requests: set[str] = set()
    active: str | None = None
    for message in messages:
        content = message.get("content") or {}
        kind = message.get("kind")
        if kind == "permission":
            request_id = str(content.get("request_id") or "")
            if request_id and content.get("status") == "resolved":
                resolved_requests.add(request_id)
            continue
        if kind == "text" and message.get("role") == "user":
            message_id = str(message["id"])
            if content.get("queued"):
                states[message_id] = "queued"
            else:
                states[message_id] = "delivered"
                active = message_id
            continue
        if kind != "status":
            continue
        event = content.get("event")
        message_id = str(content.get("message_id") or "")
        if event == "queued_delivered" and message_id:
            states[message_id] = "delivered"
            active = message_id
        elif event == "queued_dropped" and message_id:
            # Legacy claim-then-error rows may contain both proofs. Delivered
            # evidence dominates dropped unless a real turn terminal exists.
            if states.get(message_id) not in ("delivered", "unknown", "completed"):
                states[message_id] = "dropped"
                if active == message_id:
                    active = None
        elif event == "error" and content.get("outcome") == "unknown" and message_id:
            states[message_id] = "unknown"
            if active == message_id:
                active = None
        elif event == "error" and content.get("outcome") == "failed" and message_id:
            states[message_id] = "completed"
            if active == message_id:
                active = None
        elif event in ("turn_end", "turn_timeout") and not content.get("unprompted"):
            if active is not None:
                states[active] = "completed"
                active = None
        elif event == "empty_turn_retry" and message_id:
            states[message_id] = "delivered"
            active = message_id
        elif event in ("session_closed", "session_resumed"):
            active = None
        # Other errors do not prove completion. In particular, process-exit
        # and legacy fatal errors leave delivered input unknown rather than
        # inventing a successful/per-turn terminal that could authorize replay.
    return states, resolved_requests


def _append(
    service: StudioChatService,
    row: dict[str, Any],
    kind: str,
    role: str,
    content: dict[str, Any],
) -> bool:
    session_id = str(row["id"])
    appended = service.store.append_message_if_live(
        session_id,
        kind,
        role,
        content,
        _LIVE,
        error_detail_is=(
            str(row.get("error_detail") or "") if row.get("status") == "error" else None
        ),
    )
    return appended is not None


def reconcile_zombie_session(service: StudioChatService, row: dict[str, Any]) -> bool:
    """Fill only missing durable terminals; False means a successor owns the row."""
    session_id = str(row["id"])
    messages = _timeline(service, session_id)
    states, resolved_requests = _reconstruct(messages)
    for message in messages:
        if message.get("kind") != "permission":
            continue
        content = message.get("content") or {}
        request_id = str(content.get("request_id") or "")
        if not request_id or content.get("status") != "pending" or request_id in resolved_requests:
            continue
        resolved = {
            "request_id": request_id,
            "status": "resolved",
            "decision": {"deny": True, "via": "backend_restart"},
        }
        if not _append(service, row, "permission", "user", resolved):
            return False
    for message in messages:
        if message.get("kind") != "text" or message.get("role") != "user":
            continue
        message_id = str(message["id"])
        state = states.get(message_id)
        if state == "queued":
            content = {
                "event": "queued_dropped",
                "message_id": message_id,
                "detail": RESTART_DROPPED,
            }
            if not _append(service, row, "status", "system", content):
                return False
        elif state == "delivered":
            content = {
                "event": "error",
                "message_id": message_id,
                "outcome": "unknown",
                "detail": RESTART_UNKNOWN,
            }
            if not _append(service, row, "status", "system", content):
                return False
    return True


def reconcile_zombie_sessions(service: StudioChatService) -> None:
    """Run one full startup pass, failing closed if any row is unsettled."""
    logger = logging.getLogger(__name__)
    reaped = 0
    failures: list[str] = []
    for row in service.db.list_zombie_studio_chat_sessions():
        session_id = str(row["id"])
        try:
            if not reconcile_zombie_session(service, row):
                continue
            if service.db.update_studio_chat_session_if(
                session_id,
                status_in=("starting", "idle", "running", "awaiting_permission"),
                status="error",
                error_detail=RESTART_ERROR_DETAIL,
                visible_only=True,
            ):
                reaped += 1
            service.store.publish_session(session_id)
        except Exception:
            # #204 broad-except audit: repair all other rows, then fail startup
            # so no resume can replay a row whose terminal settlement failed.
            failures.append(session_id)
            logger.warning("failed to reconcile studio chat session %s", session_id, exc_info=True)
    if reaped:
        logger.warning("reaped %d zombie studio chat session(s) at startup", reaped)
    if failures:
        raise RuntimeError(
            f"studio chat startup reconciliation failed for {len(failures)} session(s)"
        )
