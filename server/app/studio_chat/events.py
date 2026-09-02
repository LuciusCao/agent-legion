"""ACP callback state machine for Studio chat sessions (issue #196).

The six ``on_*`` entry points run on the session's ACP thread and advance the
persisted session status + message timeline. They are split out of
``service.py`` behind a narrow protocol (``ServiceBackend``) so the state
machine owns no registry and no lifecycle wiring — it reads/writes through the
store and the service's runtime lookup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from server.app.studio_chat.mcp_hint import is_agent_legion_tool_call, maybe_emit_mcp_hint
from server.app.studio_chat.permissions import handle_permission_request
from server.app.studio_chat.runtime import SessionRuntime
from server.app.studio_chat.session_config_state import apply_config_update, session_config_fields
from server.app.studio_chat.store import StudioChatStore

if TYPE_CHECKING:
    from server.app.jobs import JobQueries
    from server.app.studio_chat.session_config_state import OpenedAcpSession

_EXIT_DETAIL = "agent process exited"


class ServiceBackend(Protocol):
    """What the callback state machine needs from the service facade."""

    @property
    def db(self) -> JobQueries: ...

    @property
    def store(self) -> StudioChatStore: ...

    def runtime(self, session_id: str) -> SessionRuntime | None: ...

    def teardown_runtime(
        self,
        session_id: str,
        runtime: SessionRuntime | None,
        *,
        close_handle: bool = True,
        expected: SessionRuntime | None = None,
    ) -> bool: ...


class AcpEventHandlers:
    """Session-thread callbacks: status transitions + message timeline."""

    def __init__(self, backend: ServiceBackend) -> None:
        self._backend = backend

    def on_ready(
        self, session_id: str, capabilities: dict[str, Any], opened: OpenedAcpSession
    ) -> None:
        # A close that raced the startup window already owns the final status;
        # readiness must not resurrect a closed (or failed) session. The guard
        # rides the UPDATE itself (#158): a re-read-then-write would leave a
        # check-and-set window for close to land in between. The agent config
        # surface fields (#368) ride the same UPDATE (session_config.py).
        self._backend.db.update_studio_chat_session_if(
            session_id,
            status_not_in=("closed", "error"),
            status="idle",
            **session_config_fields(capabilities, opened),
        )

    def on_update(self, session_id: str, update: dict[str, Any]) -> None:
        kind = update.get("sessionUpdate")
        runtime = self._backend.runtime(session_id)
        if kind in ("agent_message_chunk", "agent_thought_chunk"):
            text = str((update.get("content") or {}).get("text") or "")
            slot = "thought" if kind == "agent_thought_chunk" else "text"
            if runtime is not None:
                self._backend.store.append_stream_chunk(session_id, runtime, slot, text)
            return
        if kind in ("tool_call", "tool_call_update"):
            # A tool call interrupts the agent's prose: close the open
            # text/thought slots so the next chunk starts a fresh message row
            # below the tool card instead of folding into the row above it.
            if runtime is not None:
                with runtime.lock:
                    runtime.stream.reset()
            if is_agent_legion_tool_call(update) and runtime is not None:
                with runtime.lock:
                    if not runtime.mcp_observed:
                        runtime.mcp_observed = True
                self._backend.store.mark_mcp_verified(session_id)
            self._backend.store.append_message(session_id, "tool_call", "agent", update)
            return
        if kind and str(kind).startswith("plan"):
            if runtime is not None:
                with runtime.lock:
                    runtime.stream.reset()
            self._backend.store.append_message(session_id, "plan", "agent", update)
            return
        if apply_config_update(self._backend, session_id, update):
            return  # mode/config mirror rewrites (#368, session_config.py)
        # user_message_chunk / usage updates: not persisted.

    def on_permission_request(
        self, session_id: str, tool_call: dict[str, Any], options: list[dict[str, Any]]
    ) -> dict[str, Any]:
        runtime = self._backend.runtime(session_id)
        if runtime is not None:
            with runtime.lock:
                runtime.stream.reset()
        return handle_permission_request(self._backend, session_id, tool_call, options)

    def on_turn_end(self, session_id: str, stop_reason: str) -> None:
        # The MCP-visibility smoke signal is advisory only (mcp_hint.py): a
        # turn without an agent-legion tool call is not evidence of a wiring
        # problem, so the hint fires once per session, never on cancels.
        maybe_emit_mcp_hint(self._backend, session_id, stop_reason)
        self._backend.store.append_message(
            session_id, "status", "system", {"event": "turn_end", "stop_reason": stop_reason}
        )
        # Guarded (#158): turn_end only moves a live turn back to idle; a
        # concurrent close/error owns the final state.
        self._backend.db.update_studio_chat_session_if(
            session_id, status_in=("running", "awaiting_permission"), status="idle"
        )
        self._backend.store.publish_session(session_id)

    def on_error(self, session_id: str, detail: str, *, fatal: bool) -> None:
        self._backend.store.append_message(
            session_id, "status", "system", {"event": "error", "detail": detail}
        )
        if fatal:
            # Guarded (#158): a close owns the final 'closed' state. Runtime
            # teardown happens in on_exit, which the ACP thread always runs
            # after this callback.
            self._backend.db.update_studio_chat_session_if(
                session_id, status_not_in=("closed",), status="error", error_detail=detail[:500]
            )
        else:
            self._backend.db.update_studio_chat_session_if(
                session_id, status_in=("running", "awaiting_permission"), status="idle"
            )
        self._backend.store.publish_session(session_id)

    def on_exit(
        self, session_id: str, *, close_initiated: bool, expected: SessionRuntime | None
    ) -> None:
        # Agent death teardown (#158): runs on the ACP thread itself, so the
        # handle close must be skipped (self-join); the subprocess is already
        # gone. Still pops the registry entry, settles parked permissions, and
        # revokes the scoped token instead of leaving them to the TTL/timeout
        # backstops. Idempotent: a close-initiated teardown already popped the
        # runtime, making this a no-op. expected pins this thread's own
        # runtime: resume can register a NEW runtime for the same session
        # while the old thread's exit echo is still in flight — a stale echo
        # tears down only its own runtime, never the registry's current one.
        owned = self._backend.teardown_runtime(
            session_id, self._backend.runtime(session_id), close_handle=False, expected=expected
        )
        # close_initiated: the exit came from handle.close() (close/shutdown/
        # resume's winner-side teardown), not from an agent death — stamping
        # the row error here would clobber the resume claim's 'starting' row
        # and add a bogus error row to the timeline.
        if close_initiated:
            return
        if expected is not None and not owned:
            # Stale echo: a newer runtime generation owns the registry (and
            # the row's status transitions) — never stamp the row from here.
            return
        # 'starting' is the resume claim's row: a stale death echo from the
        # old thread does not own it either (same ABA window as the runtime).
        final_statuses = ("closed", "error", "starting")
        current = self._backend.db.get_studio_chat_session(session_id) or {}
        if current.get("status") in final_statuses:
            return
        self._backend.db.update_studio_chat_session_if(
            session_id, status_not_in=final_statuses, status="error", error_detail=_EXIT_DETAIL
        )
        self._backend.store.append_message(
            session_id, "status", "system", {"event": "error", "detail": _EXIT_DETAIL}
        )
        self._backend.store.publish_session(session_id)
