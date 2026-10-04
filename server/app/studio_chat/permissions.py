"""Permission policy for Studio chat sessions (split from service.py, file budget).

Decision order for an ACP permission request:
1. agent-legion MCP tool calls auto-approve — the session's workspace-bound
   scoped token is already the authority boundary (STUDIO-AGENT-001);
2. local read-only ACP kinds (``read`` / ``search`` — the Read/Glob/Grep
   class) auto-approve only when every declared target stays inside the
   workspace staging directory (minimal set + rationale: permission_scope.py);
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
from server.app.studio_chat.permission_scope import is_staging_read_only_tool_call
from server.app.studio_chat.runtime import PendingPermission

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend
    from server.app.studio_chat.runtime import SessionRuntime

logger = logging.getLogger(__name__)

PERMISSION_TIMEOUT_SECONDS = 120


def handle_permission_request(
    backend: ServiceBackend,
    session_id: str,
    tool_call: dict[str, Any],
    options: list[dict[str, Any]],
    *,
    expected: SessionRuntime | None = None,
) -> dict[str, Any]:
    """Apply the permission policy; blocks on the human answer when parked."""
    from server.app.studio_chat.mcp_hint import is_agent_legion_tool_call

    runtime = expected or backend.runtime(session_id)
    if runtime is None:
        return {"deny": True}
    request_id = uuid4().hex
    pending = PendingPermission(request_id)
    with runtime.lock:
        # Teardown flips `closed` under this same lock before its settle
        # sweep; parking after that point would hang until the timeout (#158).
        if runtime.closed or backend.runtime(session_id) is not runtime:
            return {"deny": True}
        runtime.stream.reset()
        if is_agent_legion_tool_call(tool_call):
            runtime.mcp_observed = True
            backend.store.mark_mcp_verified(session_id)
            return auto_approve(backend, session_id, tool_call, options, decision="auto_approved")
        session = backend.db.get_studio_chat_session(session_id) or {}
        if is_staging_read_only_tool_call(
            tool_call,
            options,
            workspace_id=str(session.get("workspace_id") or ""),
            cwd=runtime.handle.cwd,
        ):
            return auto_approve(backend, session_id, tool_call, options, decision="auto_read_only")
        if session.get("allow_all_permissions"):
            return auto_approve(backend, session_id, tool_call, options, decision="allow_all")
        runtime.pending_permissions[request_id] = pending
        try:
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
            parked = backend.db.update_studio_chat_session_if(
                session_id,
                status_in=("running", "awaiting_permission"),
                status="awaiting_permission",
            )
        except BaseException:
            # #204 broad-except audit: remove only this request's local waiter
            # on failed admission, then re-raise unchanged; no failure is swallowed.
            runtime.pending_permissions.pop(request_id, None)
            raise
        if not parked:
            runtime.pending_permissions.pop(request_id, None)
            pending.decision = {"deny": True, "via": "session_closed"}
        else:
            backend.store.publish_session(session_id)
    if parked:
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
                if (
                    not runtime.closed
                    and backend.runtime(session_id) is runtime
                    and not still_parked
                    and backend.db.update_studio_chat_session_if(
                        session_id, status_in=("awaiting_permission",), status="running"
                    )
                ):
                    backend.store.publish_session(session_id)
    with runtime.lock:
        if runtime.closed or backend.runtime(session_id) is not runtime:
            return {"deny": True, "via": "session_closed"}
        backend.store.append_message(
            session_id,
            "permission",
            "user",
            {"request_id": request_id, "status": "resolved", "decision": pending.decision},
        )
        return pending.decision


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
    # `via` rides on the ACP-side outcome too: platform auto-approvals never
    # authorize a terminal/create (terminal_policy.py), only human/allow-all.
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
    return {**outcome, "via": decision}
