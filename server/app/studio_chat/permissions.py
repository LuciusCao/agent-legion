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
    from server.app.studio_chat.events import ServiceBackend

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
    backend: ServiceBackend,
    session_id: str,
    tool_call: dict[str, Any],
    options: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply the permission policy; blocks on the human answer when parked."""
    from server.app.studio_chat.events import is_agent_legion_tool_call

    if is_agent_legion_tool_call(tool_call):
        runtime = backend.runtime(session_id)
        if runtime is not None:
            with runtime.lock:
                runtime.mcp_observed = True
        backend.store.mark_mcp_verified(session_id)
        return auto_approve(backend, session_id, tool_call, options, decision="auto_approved")
    if is_read_only_tool_call(tool_call):
        return auto_approve(backend, session_id, tool_call, options, decision="auto_read_only")
    session = backend.db.get_studio_chat_session(session_id) or {}
    if session.get("allow_all_permissions"):
        return auto_approve(backend, session_id, tool_call, options, decision="allow_all")
    request_id = uuid4().hex
    pending = PendingPermission(request_id)
    runtime = backend.runtime(session_id)
    if runtime is None:
        return {"deny": True}
    with runtime.lock:
        # Teardown flips `closed` under this same lock before its settle
        # sweep; parking after that point would hang until the timeout (#158).
        if runtime.closed:
            return {"deny": True}
        runtime.pending_permissions[request_id] = pending
    backend.store.append_message(
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
    # Atomic check-and-set (#158): an unconditional write could overwrite a
    # concurrent close/error back to a live state. 'awaiting_permission' is an
    # allowed current state because concurrent prompts of the same turn
    # re-park. When the guard fails the session is closing or dead: deny at
    # once instead of parking against a torn-down runtime.
    parked = backend.db.update_studio_chat_session_if(
        session_id,
        status_in=("running", "awaiting_permission"),
        status="awaiting_permission",
    )
    if not parked:
        with runtime.lock:
            runtime.pending_permissions.pop(request_id, None)
        pending.decision = {"deny": True, "via": "session_closed"}
    else:
        backend.store.publish_session(session_id)
        try:
            settled = pending.event.wait(timeout=PERMISSION_TIMEOUT_SECONDS)
            if not settled:
                logger.warning("studio chat permission %s timed out; auto-denied", request_id)
                with runtime.lock:
                    # Dict membership is the not-yet-settled criterion (#158):
                    # a human answer that raced the timeout already popped the
                    # request and owns the decision.
                    orphaned = runtime.pending_permissions.pop(request_id, None)
                    if orphaned is not None:
                        orphaned.decision = {"deny": True, "via": "timeout"}
        finally:
            with runtime.lock:
                runtime.pending_permissions.pop(request_id, None)
                still_parked = bool(runtime.pending_permissions)
            # Only the awaiting_permission → running transition is ours, and
            # only once no prompt of this turn is still parked: a close (or
            # fatal error) that settled this waiter must not be overwritten
            # back to running (ghost live session, #158).
            if not still_parked and backend.db.update_studio_chat_session_if(
                session_id, status_in=("awaiting_permission",), status="running"
            ):
                backend.store.publish_session(session_id)
    decision = pending.decision
    backend.store.append_message(
        session_id,
        "permission",
        "user",
        {"request_id": request_id, "status": "resolved", "decision": decision},
    )
    return decision


def auto_approve(
    backend: ServiceBackend,
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
    backend.store.append_message(
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
