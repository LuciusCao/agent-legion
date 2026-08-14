"""Studio chat session service (phase 3 chunk 4): lifecycle + run state machine.

One chat session = one ACP agent subprocess (AcpSessionHandle) plus its
persisted timeline. The service owns the registry of live handles, applies
the permission policy (agent-legion MCP tool calls auto-approve — the scoped
token is already the authority boundary; everything else goes to the human,
with a per-session allow-all switch), tracks the behavioural MCP-visibility
smoke signal (a run that never showed an agent-legion tool call ends with
mcp_status='unverified' instead of silently succeeding), and forwards
everything to SSE subscribers through the shared event bus.

All callback entry points (on_ready/on_update/...) run on the session's ACP
thread; public entry points run on FastAPI worker threads. Mutable runtime
state is guarded by a per-session lock; DB rows are the durable source of
truth for status.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from typing import Any
from uuid import uuid4

from server.app.auth.scoped_tokens import mint_scoped_token, revoke_scoped_token
from server.app.events.bus import EventBus
from server.app.jobs import JobQueries
from server.app.services.job_errors import ConflictError, InvalidOperationError, NotFoundError
from server.app.settings import Settings
from server.app.studio_chat.acp_session import AcpSessionHandle, build_mcp_server_spec
from server.app.studio_chat.availability import AgentAvailabilityProbe
from server.app.studio_chat.callbacks import ServiceCallbacks
from server.app.studio_chat.payloads import (
    pick_allow_option,
    serialize_message,
    serialize_session,
)
from server.app.studio_chat.prompts import (
    STUDIO_AUTHORING_BOOTSTRAP,
    looks_like_agent_legion_tool_call,
)
from server.app.studio_chat.registry import StudioAgentRegistryStore

logger = logging.getLogger(__name__)

# Time to wait for the agent subprocess to finish initialize + session/new.
SESSION_START_TIMEOUT_SECONDS = 60


def studio_chat_channel(session_id: str) -> str:
    return f"studio-chat:{session_id}"


class _PendingPermission:
    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.event = threading.Event()
        self.decision: dict[str, Any] = {"deny": True}


class _SessionRuntime:
    """Live (in-memory) state for one chat session; never persisted."""

    def __init__(self, handle: AcpSessionHandle, token: str) -> None:
        self.handle = handle
        # Raw scoped token, held only to revoke on close; never leaves memory.
        self.token = token
        self.lock = threading.Lock()
        self.pending_permissions: dict[str, _PendingPermission] = {}
        # Streaming agent text coalescing: chunks of one turn accumulate onto
        # a single message row (the ACP SDK resolves the prompt response
        # before the last session/update handler tasks run, so buffer-until-
        # turn-end would lose trailing chunks; an open row tolerates any
        # ordering). Turn end closes the open message.
        self.open_text_message_id: str | None = None
        self.open_text: str = ""
        self.mcp_observed = False


class StudioChatService:
    def __init__(
        self,
        job_db: JobQueries,
        settings: Settings,
        bus: EventBus | None,
        probe: AgentAvailabilityProbe | None = None,
    ) -> None:
        self._db = job_db
        self._settings = settings
        self._bus = bus
        self._registry = StudioAgentRegistryStore(job_db.path)
        self._probe = probe if probe is not None else AgentAvailabilityProbe()
        self._runtimes: dict[str, _SessionRuntime] = {}
        self._runtimes_lock = threading.Lock()
        self._shutdown = False

    # -- registry (agent catalog) ----------------------------------------

    def warm_availability_probe(self) -> None:
        """Startup probe of every registered agent command (log-only)."""
        agents = self._registry.get()["agents"]
        results = self._probe.probe_all([str(agent["command"]) for agent in agents])
        for agent in agents:
            if not results.get(str(agent["command"])):
                logger.warning(
                    "studio agent %s command not found on this host: %s",
                    agent.get("id"),
                    agent["command"],
                )

    def list_available_agents(self) -> list[dict[str, Any]]:
        """Picker view for non-admin users: id/label of agents whose command
        resolves on this host — never the command line, never missing agents."""
        document = self._registry.get()
        return [
            {"id": str(agent["id"]), "label": str(agent.get("label") or agent["id"])}
            for agent in document["agents"]
            if self._probe.available(str(agent["command"]))
        ]

    # -- session lifecycle ------------------------------------------------

    def create_session(self, workspace_id: str, user_id: str, agent_id: str) -> dict[str, Any]:
        if self._shutdown:
            raise ConflictError("Studio chat service is shutting down")
        agent = self._registry.find_agent(agent_id)
        if agent is None:
            raise InvalidOperationError(f"Unknown studio agent: {agent_id}")
        command = str(agent["command"])
        if not self._probe.available(command):
            raise InvalidOperationError(
                f"Studio agent '{agent_id}' is not available on this host"
                f" (command not found: {command})"
            )
        session_id = self._db.create_studio_chat_session(workspace_id, user_id, agent_id)
        token = mint_scoped_token(self._db, user_id, origin="run")
        handle = AcpSessionHandle(
            command=command,
            args=[str(arg) for arg in agent.get("args", [])],
            cwd=str(self._settings.root_dir),
            mcp_server=build_mcp_server_spec(
                token=token,
                api_base=str(self._registry.get()["api_base"]),
                python_executable=sys.executable,
            ),
            env=None,
            callbacks=ServiceCallbacks(self, session_id),
        )
        runtime = _SessionRuntime(handle, token)
        with self._runtimes_lock:
            self._runtimes[session_id] = runtime
        handle.start()
        if not handle.ready_event.wait(timeout=SESSION_START_TIMEOUT_SECONDS):
            self._fail_session_start(session_id, runtime, "agent startup timed out")
            raise InvalidOperationError("Studio agent failed to start (timeout)")
        session = self._db.get_studio_chat_session(session_id)
        if session is None or session["status"] != "idle":
            detail = (session or {}).get("error_detail") or "agent startup failed"
            self._fail_session_start(session_id, runtime, detail)
            raise InvalidOperationError(f"Studio agent failed to start: {detail}")
        self._publish_session(session_id)
        return session

    def _fail_session_start(self, session_id: str, runtime: _SessionRuntime, detail: str) -> None:
        self._db.update_studio_chat_session(session_id, status="error", error_detail=detail)
        self._teardown_runtime(session_id, runtime)

    def list_sessions(self, workspace_id: str) -> list[dict[str, Any]]:
        return self._db.list_studio_chat_sessions(workspace_id)

    def get_session(self, session_id: str, workspace_id: str | None = None) -> dict[str, Any]:
        session = self._db.get_studio_chat_session(session_id)
        if session is None or (
            workspace_id is not None and session["workspace_id"] != workspace_id
        ):
            raise NotFoundError("Chat session not found")
        return session

    def rename_session(self, session_id: str, workspace_id: str, title: str) -> dict[str, Any]:
        self.get_session(session_id, workspace_id)
        self._db.update_studio_chat_session(session_id, title=title)
        return self.get_session(session_id)

    def close_session(self, session_id: str, workspace_id: str) -> dict[str, Any]:
        session = self.get_session(session_id, workspace_id)
        if session["status"] == "closed":
            return session
        runtime = self._runtime(session_id)
        self._db.update_studio_chat_session(session_id, status="closed")
        self._teardown_runtime(session_id, runtime)
        self._append_message(session_id, "status", "system", {"event": "session_closed"})
        self._publish_session(session_id)
        return self.get_session(session_id)

    def _teardown_runtime(self, session_id: str, runtime: _SessionRuntime | None) -> None:
        """Deny pending permissions, stop the subprocess, revoke the token."""
        with self._runtimes_lock:
            current = self._runtimes.pop(session_id, None)
        runtime = current if current is not None else runtime
        if runtime is None:
            return
        with runtime.lock:
            for pending in runtime.pending_permissions.values():
                pending.decision = {"deny": True}
                pending.event.set()
        runtime.handle.close()
        try:
            revoke_scoped_token(self._db, runtime.token)
        except Exception:
            logger.warning("failed to revoke studio chat token for %s", session_id)

    # -- messaging ---------------------------------------------------------

    def send_message(self, session_id: str, workspace_id: str, text: str) -> dict[str, Any]:
        session = self.get_session(session_id, workspace_id)
        if session["status"] == "closed":
            raise ConflictError("Chat session is closed")
        if session["status"] != "idle":
            raise ConflictError(f"Chat session is busy ({session['status']})")
        runtime = self._runtime(session_id)
        if runtime is None:
            raise ConflictError("Chat session is not running on this server")
        first_prompt = self._db.count_studio_chat_user_messages(session_id) == 0
        message = self._append_message(session_id, "text", "user", {"text": text})
        self._db.update_studio_chat_session(session_id, status="running")
        self._publish_session(session_id)
        prompt_text = (STUDIO_AUTHORING_BOOTSTRAP + text) if first_prompt else text
        if not runtime.handle.send_prompt(prompt_text):
            self._db.update_studio_chat_session(session_id, status="error")
            raise ConflictError("Chat session agent is not running")
        return message

    def list_messages(
        self, session_id: str, workspace_id: str, *, after_seq: int = 0
    ) -> list[dict[str, Any]]:
        self.get_session(session_id, workspace_id)
        return self._db.list_studio_chat_messages(session_id, after_seq=after_seq)

    def cancel(self, session_id: str, workspace_id: str) -> dict[str, Any]:
        session = self.get_session(session_id, workspace_id)
        runtime = self._runtime(session_id)
        if runtime is not None:
            with runtime.lock:
                # A cancelled turn must not leave a permission prompt hanging:
                # settle every pending request as denied before signalling the
                # agent (AGENTS.md half-applied-state discipline).
                for pending in runtime.pending_permissions.values():
                    pending.decision = {"deny": True}
                    pending.event.set()
            runtime.handle.cancel()
        if session["status"] in ("running", "awaiting_permission"):
            self._append_message(session_id, "status", "system", {"event": "cancel_requested"})
        return self.get_session(session_id)

    def set_allow_all_permissions(
        self, session_id: str, workspace_id: str, enabled: bool
    ) -> dict[str, Any]:
        session = self.get_session(session_id, workspace_id)
        if session["status"] == "closed":
            raise ConflictError("Chat session is closed")
        self._db.update_studio_chat_session(session_id, allow_all_permissions=enabled)
        self._publish_session(session_id)
        return self.get_session(session_id)

    def respond_permission(
        self,
        session_id: str,
        workspace_id: str,
        request_id: str,
        *,
        option_id: str | None,
        deny: bool,
    ) -> None:
        self.get_session(session_id, workspace_id)
        runtime = self._runtime(session_id)
        pending = runtime.pending_permissions.get(request_id) if runtime else None
        if pending is None:
            raise NotFoundError("Permission request not found or already resolved")
        pending.decision = {"deny": True} if deny else {"option_id": option_id}
        pending.event.set()

    # -- callbacks from the ACP session thread -----------------------------

    def _on_ready(self, session_id: str, capabilities: dict[str, Any], acp_session_id: str) -> None:
        self._db.update_studio_chat_session(
            session_id,
            status="idle",
            capability_snapshot=capabilities,
            acp_session_id=acp_session_id,
        )

    def _on_update(self, session_id: str, update: dict[str, Any]) -> None:
        kind = update.get("sessionUpdate")
        runtime = self._runtime(session_id)
        if kind == "agent_message_chunk":
            text = str((update.get("content") or {}).get("text") or "")
            if runtime is not None:
                self._append_agent_chunk(session_id, runtime, text)
            return
        if kind in ("tool_call", "tool_call_update"):
            if self._is_agent_legion_tool_call(update) and runtime is not None:
                with runtime.lock:
                    if not runtime.mcp_observed:
                        runtime.mcp_observed = True
                self._mark_mcp_verified(session_id)
            self._append_message(session_id, "tool_call", "agent", update)
            return
        if kind and str(kind).startswith("plan"):
            self._append_message(session_id, "plan", "agent", update)
            return
        # user_message_chunk / thought / mode / usage updates: not persisted.

    def _on_permission_request(
        self, session_id: str, tool_call: dict[str, Any], options: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if self._is_agent_legion_tool_call(tool_call):
            runtime = self._runtime(session_id)
            if runtime is not None:
                with runtime.lock:
                    runtime.mcp_observed = True
            self._mark_mcp_verified(session_id)
            return self._auto_approve(session_id, tool_call, options, decision="auto_approved")
        session = self._db.get_studio_chat_session(session_id) or {}
        if session.get("allow_all_permissions"):
            return self._auto_approve(session_id, tool_call, options, decision="allow_all")
        request_id = uuid4().hex
        pending = _PendingPermission(request_id)
        runtime = self._runtime(session_id)
        if runtime is None:
            return {"deny": True}
        with runtime.lock:
            runtime.pending_permissions[request_id] = pending
        self._append_message(
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
        self._db.update_studio_chat_session(session_id, status="awaiting_permission")
        self._publish_session(session_id)
        try:
            pending.event.wait()
        finally:
            with runtime.lock:
                runtime.pending_permissions.pop(request_id, None)
            self._db.update_studio_chat_session(session_id, status="running")
            self._publish_session(session_id)
        decision = pending.decision
        self._append_message(
            session_id,
            "permission",
            "user",
            {"request_id": request_id, "status": "resolved", "decision": decision},
        )
        return decision

    def _auto_approve(
        self,
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
        self._append_message(
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

    def _on_turn_end(self, session_id: str, stop_reason: str) -> None:
        self._close_open_text_message(session_id)
        session = self._db.get_studio_chat_session(session_id) or {}
        runtime = self._runtime(session_id)
        mcp_observed = runtime.mcp_observed if runtime is not None else False
        if not mcp_observed and session.get("mcp_status") != "verified":
            self._db.update_studio_chat_session(session_id, mcp_status="unverified")
            self._append_message(
                session_id,
                "status",
                "system",
                {
                    "event": "mcp_unverified",
                    "detail": (
                        "This turn never called an agent-legion MCP tool; the agent may "
                        "not have picked up the platform tools."
                    ),
                },
            )
        self._append_message(
            session_id, "status", "system", {"event": "turn_end", "stop_reason": stop_reason}
        )
        current = self._db.get_studio_chat_session(session_id) or {}
        if current.get("status") in ("running", "awaiting_permission"):
            self._db.update_studio_chat_session(session_id, status="idle")
        self._publish_session(session_id)

    def _on_error(self, session_id: str, detail: str, *, fatal: bool) -> None:
        self._close_open_text_message(session_id)
        self._append_message(session_id, "status", "system", {"event": "error", "detail": detail})
        current = self._db.get_studio_chat_session(session_id) or {}
        if current.get("status") == "closed":
            return
        if fatal:
            self._db.update_studio_chat_session(
                session_id, status="error", error_detail=detail[:500]
            )
        elif current.get("status") in ("running", "awaiting_permission"):
            self._db.update_studio_chat_session(session_id, status="idle")
        self._publish_session(session_id)

    def _on_exit(self, session_id: str) -> None:
        current = self._db.get_studio_chat_session(session_id) or {}
        if current.get("status") in ("closed", "error"):
            return
        self._db.update_studio_chat_session(
            session_id, status="error", error_detail="agent process exited"
        )
        self._append_message(
            session_id, "status", "system", {"event": "error", "detail": "agent process exited"}
        )
        self._publish_session(session_id)

    # -- shutdown ------------------------------------------------------------

    def shutdown(self) -> None:
        """Close every live session (backend shutdown hook)."""
        self._shutdown = True
        with self._runtimes_lock:
            items = list(self._runtimes.items())
        for session_id, runtime in items:
            try:
                self._db.update_studio_chat_session(session_id, status="closed")
            except Exception:
                logger.warning("failed to mark studio chat session %s closed", session_id)
            self._teardown_runtime(session_id, runtime)

    # -- internals -------------------------------------------------------------

    def _runtime(self, session_id: str) -> _SessionRuntime | None:
        with self._runtimes_lock:
            return self._runtimes.get(session_id)

    def _append_agent_chunk(self, session_id: str, runtime: _SessionRuntime, text: str) -> None:
        """Fold a streamed text chunk into the turn's single agent message."""
        if not text:
            return
        with runtime.lock:
            open_id = runtime.open_text_message_id
            runtime.open_text += text
            full_text = runtime.open_text
        if open_id is None:
            message = self._append_message(session_id, "text", "agent", {"text": full_text})
            with runtime.lock:
                runtime.open_text_message_id = message["id"]
            return
        self._db.update_studio_chat_message_content(open_id, {"text": full_text})
        self._publish(
            session_id,
            {
                "type": "message",
                "message": {
                    "id": open_id,
                    "session_id": session_id,
                    "kind": "text",
                    "role": "agent",
                    "content": {"text": full_text},
                },
            },
        )

    def _close_open_text_message(self, session_id: str) -> None:
        runtime = self._runtime(session_id)
        if runtime is None:
            return
        with runtime.lock:
            runtime.open_text_message_id = None
            runtime.open_text = ""

    def _is_agent_legion_tool_call(self, payload: dict[str, Any]) -> bool:
        text = json.dumps(payload, ensure_ascii=False)
        return looks_like_agent_legion_tool_call(text)

    def _mark_mcp_verified(self, session_id: str) -> None:
        session = self._db.get_studio_chat_session(session_id) or {}
        if session.get("mcp_status") == "verified":
            return
        self._db.update_studio_chat_session(session_id, mcp_status="verified")

    def _append_message(
        self, session_id: str, kind: str, role: str, content: dict[str, Any]
    ) -> dict[str, Any]:
        message = self._db.append_studio_chat_message(session_id, kind, role, content)
        self._publish(session_id, {"type": "message", "message": serialize_message(message)})
        return message

    def _publish_session(self, session_id: str) -> None:
        session = self._db.get_studio_chat_session(session_id)
        if session is not None:
            self._publish(session_id, {"type": "session", "session": serialize_session(session)})

    def _publish(self, session_id: str, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        payload = {"session_id": session_id, **payload}
        try:
            self._bus.publish(studio_chat_channel(session_id), json.dumps(payload, default=str))
        except Exception:
            logger.warning("failed to publish studio chat event for %s", session_id)
