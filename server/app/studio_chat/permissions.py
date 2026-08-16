"""Permission policy for Studio chat sessions (split from service.py, file budget).

Decision order for an ACP permission request:
1. agent-legion MCP tool calls auto-approve — the session's workspace-bound
   scoped token is already the authority boundary (STUDIO-AGENT-001);
2. local read-only ACP kinds (``read`` / ``search`` — the Read/Glob/Grep
   class) auto-approve as side-effect-free;
3. the per-session allow-all switch approves everything else without a
   roundtrip;
4. otherwise the request parks for a human answer, and an unanswered prompt
   (browser closed, tab abandoned) is auto-denied after the timeout instead
   of parking the ACP thread-pool thread and the agent subprocess forever.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from server.app.studio_chat.payloads import pick_allow_option
from server.app.studio_chat.runtime import PendingPermission

if TYPE_CHECKING:
    from server.app.studio_chat.service import StudioChatService

logger = logging.getLogger(__name__)

PERMISSION_TIMEOUT_SECONDS = 120

# ACP ToolKind values that are local and read-only (the Read/Glob/Grep class).
# Write/execute kinds (edit, delete, move, execute, fetch, ...) still require
# human confirmation; a false negative only degrades to the human path.
READ_ONLY_TOOL_KINDS = frozenset({"read", "search"})


def is_read_only_tool_call(tool_call: dict[str, Any]) -> bool:
    """Whether the ACP tool call is a local read-only kind (auto-approvable)."""
    return str(tool_call.get("kind") or "") in READ_ONLY_TOOL_KINDS


def handle_permission_request(
    service: StudioChatService,
    session_id: str,
    tool_call: dict[str, Any],
    options: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply the permission policy; blocks on the human answer when parked."""
    if service._is_agent_legion_tool_call(tool_call):
        runtime = service._runtime(session_id)
        if runtime is not None:
            with runtime.lock:
                runtime.mcp_observed = True
        service._mark_mcp_verified(session_id)
        return auto_approve(service, session_id, tool_call, options, decision="auto_approved")
    if is_read_only_tool_call(tool_call):
        return auto_approve(service, session_id, tool_call, options, decision="auto_read_only")
    session = service._db.get_studio_chat_session(session_id) or {}
    if session.get("allow_all_permissions"):
        return auto_approve(service, session_id, tool_call, options, decision="allow_all")
    request_id = uuid4().hex
    pending = PendingPermission(request_id)
    runtime = service._runtime(session_id)
    if runtime is None:
        return {"deny": True}
    with runtime.lock:
        runtime.pending_permissions[request_id] = pending
    service._append_message(
        session_id,
        "permission",
        "agent",
        {
            "request_id": request_id,
            "status": "pending",
            "tool_call": tool_call,
            "options": options,
        },
    )
    service._db.update_studio_chat_session(session_id, status="awaiting_permission")
    service._publish_session(session_id)
    try:
        settled = pending.event.wait(timeout=PERMISSION_TIMEOUT_SECONDS)
        if not settled:
            logger.warning("studio chat permission %s timed out; auto-denied", request_id)
            pending.decision = {"deny": True, "via": "timeout"}
    finally:
        with runtime.lock:
            runtime.pending_permissions.pop(request_id, None)
        # Only the awaiting_permission → running transition is ours: a close
        # (or fatal error) that settled this waiter as denied must not be
        # overwritten back to running (ghost live session).
        current = service._db.get_studio_chat_session(session_id) or {}
        if current.get("status") == "awaiting_permission":
            service._db.update_studio_chat_session(session_id, status="running")
            service._publish_session(session_id)
    decision = pending.decision
    service._append_message(
        session_id,
        "permission",
        "user",
        {"request_id": request_id, "status": "resolved", "decision": decision},
    )
    return decision


def auto_approve(
    service: StudioChatService,
    session_id: str,
    tool_call: dict[str, Any],
    options: list[dict[str, Any]],
    *,
    decision: str,
) -> dict[str, Any]:
    option = pick_allow_option(options)
    if option is None:
        outcome: dict[str, Any] = {"deny": True}
    else:
        outcome = {"option_id": option["optionId"]}
    service._append_message(
        session_id,
        "permission",
        "system",
        {
            "status": "resolved",
            "decision": {**outcome, "via": decision},
            "tool_call": tool_call,
        },
    )
    return outcome
