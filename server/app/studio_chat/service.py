"""Studio chat session service (phase 3 chunk 4): lifecycle + run state machine.

One chat session = one ACP agent subprocess (AcpSessionHandle) plus its
persisted timeline. The service owns the registry of live handles, delegates
the permission policy to studio_chat.permissions (MCP tool calls auto-approve —
the scoped token is already the authority boundary; local read-only kinds
auto-approve; everything else goes to the human, with a per-session allow-all
switch), tracks the behavioural MCP-visibility smoke signal (a session where
no turn ever showed an agent-legion tool call gets a one-time advisory hint
after its first completed turn — cancelled turns and no-tool Q&A turns are
not treated as wiring failures), and forwards everything to SSE subscribers
through the shared event bus.

All callback entry points (on_ready/on_update/...) run on the session's ACP
thread (delegated to ``AcpEventHandlers``); public entry points run on
FastAPI worker threads. Durable writes + publishes go through
``StudioChatStore``. Mutable runtime state is guarded by a per-session lock;
DB rows are the durable source of truth for status.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from server.app.auth.scoped_tokens import (
    mint_scoped_token,
    renew_scoped_token,
    revoke_scoped_token,
)
from server.app.events.bus import EventBus
from server.app.jobs import JobQueries
from server.app.services.job_errors import ConflictError, InvalidOperationError, NotFoundError
from server.app.settings import Settings
from server.app.studio_chat.acp_session import AcpSessionHandle, build_mcp_server_spec
from server.app.studio_chat.availability import AgentAvailabilityProbe
from server.app.studio_chat.callbacks import ServiceCallbacks
from server.app.studio_chat.registry import StudioAgentRegistryStore
from server.app.studio_chat.runtime import (
    SessionRuntime,
    teardown_runtime,
)
from server.app.studio_chat.store import StudioChatStore

if TYPE_CHECKING:
    from server.app.studio_chat.events import AcpEventHandlers

logger = logging.getLogger(__name__)

# Time to wait for the agent subprocess to finish initialize + session/new.
SESSION_START_TIMEOUT_SECONDS = 60

# Global cap on live chat sessions (#158): every session owns an agent
# subprocess, a dedicated thread/event loop, and a minted scoped token, so
# unbounded creation is a resource-DoS surface. Closed/errored sessions do
# not count; the check re-reads the DB at create time.
MAX_ACTIVE_STUDIO_CHAT_SESSIONS = 32


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
        self._registry = StudioAgentRegistryStore(job_db)
        self._probe = probe if probe is not None else AgentAvailabilityProbe()
        self._runtimes: dict[str, SessionRuntime] = {}
        self._runtimes_lock = threading.Lock()
        self._shutdown = False
        self.store = StudioChatStore(job_db, bus)

    # ServiceBackend protocol surface (events.py / permissions.py consumers).

    @property
    def db(self) -> JobQueries:
        return self._db

    def runtime(self, session_id: str) -> SessionRuntime | None:
        with self._runtimes_lock:
            return self._runtimes.get(session_id)

    def teardown_runtime(
        self, session_id: str, runtime: SessionRuntime | None, *, close_handle: bool = True
    ) -> None:
        teardown_runtime(
            self._db,
            self._runtimes,
            self._runtimes_lock,
            session_id,
            runtime,
            close_handle=close_handle,
        )

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
        # The cap check rides the same transaction as the INSERT under an
        # advisory lock (#158 review): a separate count-then-insert would let
        # concurrent creators all pass and each spawn a subprocess.
        session_id = self._db.create_studio_chat_session(
            workspace_id, user_id, agent_id, max_active=MAX_ACTIVE_STUDIO_CHAT_SESSIONS
        )
        if session_id is None:
            raise ConflictError(
                f"Too many active studio chat sessions (limit {MAX_ACTIVE_STUDIO_CHAT_SESSIONS});"
                " close an existing session first"
            )
        token: str | None = None
        runtime: SessionRuntime | None = None
        try:
            # The run token is bound to this session's workspace (schema v45):
            # the tool surface then refuses other workspaces for it.
            token = mint_scoped_token(self._db, user_id, origin="run", workspace_id=workspace_id)
            handle = AcpSessionHandle(
                command=command,
                args=[str(arg) for arg in agent.get("args", [])],
                cwd=str(self._settings.root_dir),
                mcp_server=build_mcp_server_spec(
                    token=token,
                    api_base=str(self._registry.get()["api_base"]),
                    session_id=session_id,
                ),
                env=None,
                callbacks=ServiceCallbacks(self, session_id),
            )
            runtime = SessionRuntime(handle, token)
            with self._runtimes_lock:
                self._runtimes[session_id] = runtime
            handle.start()
            if not handle.ready_event.wait(timeout=SESSION_START_TIMEOUT_SECONDS):
                raise InvalidOperationError("Studio agent failed to start (timeout)")
            session = self._db.get_studio_chat_session(session_id)
            if session is None or session["status"] != "idle":
                detail = (session or {}).get("error_detail") or "agent startup failed"
                raise InvalidOperationError(f"Studio agent failed to start: {detail}")
        except Exception as exc:
            # One cleanup path for every startup failure: no half-applied
            # 'starting' row, no leaked token, no orphaned runtime. The status
            # write is guarded (#158): a close that raced the startup window
            # already owns the final 'closed' state.
            # #204 broad-except audit: the catch stays broad because the
            # outcome space is genuinely mixed — expected startup refusals
            # (InvalidOperationError for agent-not-available / start timeout)
            # AND programming errors must both run this compensation, and both
            # keep propagating via the bare re-raise (the original type is
            # never converted, so the two stay distinguishable to the caller
            # and the route layer). This mirrors the #233 pattern: compensate
            # broad, classify never.
            detail = str(exc) or exc.__class__.__name__
            self._db.update_studio_chat_session_if(
                session_id, status_not_in=("closed",), status="error", error_detail=detail[:500]
            )
            if runtime is None:
                # The handle never materialized: nothing to tear down, but a
                # minted token must not leak (token is None when the mint
                # itself failed).
                if token is not None:
                    try:
                        revoke_scoped_token(self._db, token)
                    except Exception:
                        # #204: teardown safety net (same semantics as
                        # runtime.teardown): a failed revoke must not mask
                        # the startup failure that is already propagating.
                        # The token TTL plus the maintenance purge are the
                        # backstop; exc_info keeps the revoke root cause
                        # visible for operators.
                        logger.warning(
                            "failed to revoke studio chat token for %s",
                            session_id,
                            exc_info=True,
                        )
            else:
                self.teardown_runtime(session_id, runtime)
            raise
        self.store.publish_session(session_id)
        return session

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
        runtime = self.runtime(session_id)
        self._db.update_studio_chat_session(
            session_id, status="closed", closed_at=datetime.now(UTC)
        )
        self.teardown_runtime(session_id, runtime)
        self.store.append_message(session_id, "status", "system", {"event": "session_closed"})
        self.store.publish_session(session_id)
        return self.get_session(session_id)

    # -- messaging ---------------------------------------------------------

    def send_message(self, session_id: str, workspace_id: str, text: str) -> dict[str, Any]:
        session = self.get_session(session_id, workspace_id)
        if session["status"] == "closed":
            raise ConflictError("Chat session is closed")
        runtime = self.runtime(session_id)
        if runtime is None:
            raise ConflictError("Chat session is not running on this server")
        first_prompt = self._db.count_studio_chat_user_messages(session_id) == 0
        # Atomic idle -> running claim: two concurrent senders (double click,
        # two clients) cannot both observe idle and start duplicate turns.
        if not self._db.claim_studio_chat_turn(session_id):
            current = self._db.get_studio_chat_session(session_id) or {}
            status = str(current.get("status", "unknown"))
            if status == "closed":
                raise ConflictError("Chat session is closed")
            raise ConflictError(f"Chat session is busy ({status})")
        # Token renewal at turn start (#158): chat sessions outlive the fixed
        # scoped-token TTL and the agent's MCP headers cannot be re-pointed
        # mid-session, so a still-live token near expiry is slid forward.
        renew_scoped_token(self._db, runtime.token)
        # New turn, new stream rows: reset the coalescing slots at turn START
        # (not at turn end) so trailing chunks of the finished turn — the ACP
        # SDK can deliver them after turn_end — keep folding into that turn's
        # rows instead of starting tail-only orphan rows (#98).
        with runtime.lock:
            runtime.stream.reset()
        message = self.store.append_message(session_id, "text", "user", {"text": text})
        self.store.publish_session(session_id)
        from server.app.studio_chat.prompts import STUDIO_AUTHORING_BOOTSTRAP

        prompt_text = (STUDIO_AUTHORING_BOOTSTRAP + text) if first_prompt else text
        if not runtime.handle.send_prompt(prompt_text):
            # Guarded (#158): a close racing this turn owns the final state.
            self._db.update_studio_chat_session_if(
                session_id, status_not_in=("closed",), status="error"
            )
            raise ConflictError("Chat session agent is not running")
        return message

    def list_messages(
        self, session_id: str, workspace_id: str, *, after_seq: int = 0
    ) -> list[dict[str, Any]]:
        self.get_session(session_id, workspace_id)
        return self._db.list_studio_chat_messages(session_id, after_seq=after_seq)

    def cancel(self, session_id: str, workspace_id: str) -> dict[str, Any]:
        session = self.get_session(session_id, workspace_id)
        runtime = self.runtime(session_id)
        if runtime is not None:
            with runtime.lock:
                # A cancelled turn must not leave a permission prompt hanging:
                # settle every pending request as denied before signalling the
                # agent (AGENTS.md half-applied-state discipline). Pop each
                # entry so a respond racing the cancel finds it gone (#158).
                while runtime.pending_permissions:
                    _request_id, pending = runtime.pending_permissions.popitem()
                    pending.decision = {"deny": True}
                    pending.event.set()
            runtime.handle.cancel()
        if session["status"] in ("running", "awaiting_permission"):
            self.store.append_message(session_id, "status", "system", {"event": "cancel_requested"})
        return self.get_session(session_id)

    def set_allow_all_permissions(
        self, session_id: str, workspace_id: str, enabled: bool
    ) -> dict[str, Any]:
        session = self.get_session(session_id, workspace_id)
        if session["status"] == "closed":
            raise ConflictError("Chat session is closed")
        self._db.update_studio_chat_session(session_id, allow_all_permissions=enabled)
        self.store.publish_session(session_id)
        return self.get_session(session_id)

    def set_selected_node(
        self, session_id: str, workspace_id: str, node_key: str | None
    ) -> dict[str, Any]:
        """Record the human's live Studio node selection on the session row;
        the session's agent reads it back via the get_studio_context tool."""
        self.get_session(session_id, workspace_id)
        # No SSE publish: the selection only feeds get_studio_context (live DB
        # read); the pushing client already knows the value.
        self._db.update_studio_chat_session(session_id, selected_node_key=node_key)
        return self.get_session(session_id)

    def set_draft_yaml(self, session_id: str, workspace_id: str, draft_yaml: str) -> dict[str, Any]:
        self.get_session(session_id, workspace_id)
        # No SSE publish (same reasoning as set_selected_node): the draft only
        # feeds get_studio_context; the pushing client already knows the value.
        self._db.update_studio_chat_session(session_id, draft_yaml=draft_yaml)
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
        runtime = self.runtime(session_id)
        if runtime is None:
            raise NotFoundError("Permission request not found or already resolved")
        with runtime.lock:
            # Dict membership is the not-yet-settled criterion (#158): the
            # timeout/teardown/cancel settlers pop under this same lock, so a
            # respond that loses the race finds the request gone and 404s
            # instead of writing an orphan decision and faking success.
            pending = runtime.pending_permissions.pop(request_id, None)
            if pending is None:
                raise NotFoundError("Permission request not found or already resolved")
            pending.decision = {"deny": True} if deny else {"option_id": option_id}
            pending.event.set()

    # -- callbacks from the ACP session thread (events.py) -----------------

    def _on_ready(self, session_id: str, capabilities: dict[str, Any], acp_session_id: str) -> None:
        self._events().on_ready(session_id, capabilities, acp_session_id)

    def _on_update(self, session_id: str, update: dict[str, Any]) -> None:
        self._events().on_update(session_id, update)

    def _on_permission_request(
        self, session_id: str, tool_call: dict[str, Any], options: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return self._events().on_permission_request(session_id, tool_call, options)

    def _on_turn_end(self, session_id: str, stop_reason: str) -> None:
        self._events().on_turn_end(session_id, stop_reason)

    def _on_error(self, session_id: str, detail: str, *, fatal: bool) -> None:
        self._events().on_error(session_id, detail, fatal=fatal)

    def _on_exit(self, session_id: str) -> None:
        self._events().on_exit(session_id)

    def _events(self) -> AcpEventHandlers:
        from server.app.studio_chat.events import AcpEventHandlers

        return AcpEventHandlers(self)

    # -- shutdown ------------------------------------------------------------

    def reap_zombie_sessions(self) -> None:
        """Startup reconcile (#158 review): sessions are in-process only, so
        rows left in a live status by a crashed or killed backend are
        zombies — their runtimes, tokens' owners, and agent subprocesses are
        gone. Marking them error at startup keeps the active-session cap
        honest and the session list truthful."""
        reaped = self._db.reap_zombie_studio_chat_sessions()
        if reaped:
            logger.warning("reaped %d zombie studio chat session(s) at startup", reaped)

    def shutdown(self) -> None:
        """Close every live session (backend shutdown hook)."""
        self._shutdown = True
        with self._runtimes_lock:
            items = list(self._runtimes.items())
        for session_id, runtime in items:
            try:
                self._db.update_studio_chat_session(
                    session_id, status="closed", closed_at=datetime.now(UTC)
                )
            except Exception:
                # #204 broad-except audit: shutdown safety net. The shutdown
                # loop must reach every live session — one failing status
                # write (e.g. DB already closing) must not skip the teardown
                # of the remaining subprocesses and tokens. The row stays
                # non-closed but is marked by the next startup's
                # reap_zombie_sessions, so the state self-heals.
                logger.warning(
                    "failed to mark studio chat session %s closed", session_id, exc_info=True
                )
            self.teardown_runtime(session_id, runtime)
