"""ACP session-thread callback handlers for StudioChatService.

Split from service.py (file budget): the six ``_on_*`` entry points the ACP
session thread invokes (readiness, streamed updates, permission requests,
turn end, errors, process exit). Mixed into StudioChatService, so ``self``
carries the full service surface; the self annotation keeps mypy honest
without a runtime import cycle.
"""

# mypy: disable-error-code="misc"
# The mixin annotates self as StudioChatService (its only consumer); mypy's
# "erased type of self is not a supertype" misc error is the price of that
# tighter-than-class self type — a Protocol surface cannot work here because
# permissions/mcp_hint already take the concrete service.

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.studio_chat.mcp_hint import maybe_emit_mcp_hint
from server.app.studio_chat.permissions import handle_permission_request

if TYPE_CHECKING:
    from server.app.studio_chat.service import StudioChatService


class StudioChatEventHandlers:
    """``_on_*`` callbacks; all run on the session's ACP thread."""

    def _on_ready(
        self: StudioChatService,
        session_id: str,
        capabilities: dict[str, Any],
        acp_session_id: str,
    ) -> None:
        # A close that raced the startup window already owns the final status;
        # readiness must not resurrect a closed (or failed) session. The guard
        # rides the UPDATE itself (#158): a re-read-then-write would leave a
        # check-and-set window for close to land in between.
        self._db.update_studio_chat_session_if(
            session_id,
            status_not_in=("closed", "error"),
            status="idle",
            capability_snapshot=capabilities,
            acp_session_id=acp_session_id,
        )

    def _on_update(self: StudioChatService, session_id: str, update: dict[str, Any]) -> None:
        kind = update.get("sessionUpdate")
        runtime = self._runtime(session_id)
        if kind in ("agent_message_chunk", "agent_thought_chunk"):
            text = str((update.get("content") or {}).get("text") or "")
            slot = "thought" if kind == "agent_thought_chunk" else "text"
            if runtime is not None:
                self._append_stream_chunk(session_id, runtime, slot, text)
            return
        if kind in ("tool_call", "tool_call_update"):
            # A tool call interrupts the agent's prose: close the open
            # text/thought slots so the next chunk starts a fresh message row
            # below the tool card instead of folding into the row above it.
            if runtime is not None:
                with runtime.lock:
                    runtime.stream.reset()
            if self._is_agent_legion_tool_call(update) and runtime is not None:
                with runtime.lock:
                    if not runtime.mcp_observed:
                        runtime.mcp_observed = True
                self._mark_mcp_verified(session_id)
            self._append_message(session_id, "tool_call", "agent", update)
            return
        if kind and str(kind).startswith("plan"):
            if runtime is not None:
                with runtime.lock:
                    runtime.stream.reset()
            self._append_message(session_id, "plan", "agent", update)
            return
        # user_message_chunk / mode / usage updates: not persisted.

    def _on_permission_request(
        self: StudioChatService,
        session_id: str,
        tool_call: dict[str, Any],
        options: list[dict[str, Any]],
    ) -> dict[str, Any]:
        runtime = self._runtime(session_id)
        if runtime is not None:
            with runtime.lock:
                runtime.stream.reset()
        return handle_permission_request(self, session_id, tool_call, options)

    def _on_turn_end(self: StudioChatService, session_id: str, stop_reason: str) -> None:
        # The MCP-visibility smoke signal is advisory only (mcp_hint.py): a
        # turn without an agent-legion tool call is not evidence of a wiring
        # problem, so the hint fires once per session, never on cancels.
        maybe_emit_mcp_hint(self, session_id, stop_reason)
        self._append_message(
            session_id, "status", "system", {"event": "turn_end", "stop_reason": stop_reason}
        )
        # Guarded (#158): turn_end only moves a live turn back to idle; a
        # concurrent close/error owns the final state.
        self._db.update_studio_chat_session_if(
            session_id, status_in=("running", "awaiting_permission"), status="idle"
        )
        self._publish_session(session_id)

    def _on_error(self: StudioChatService, session_id: str, detail: str, *, fatal: bool) -> None:
        self._append_message(session_id, "status", "system", {"event": "error", "detail": detail})
        if fatal:
            # Guarded (#158): a close owns the final 'closed' state. Runtime
            # teardown happens in _on_exit, which the ACP thread always runs
            # after this callback.
            self._db.update_studio_chat_session_if(
                session_id, status_not_in=("closed",), status="error", error_detail=detail[:500]
            )
        else:
            self._db.update_studio_chat_session_if(
                session_id, status_in=("running", "awaiting_permission"), status="idle"
            )
        self._publish_session(session_id)

    def _on_exit(self: StudioChatService, session_id: str) -> None:
        # Agent death teardown (#158): runs on the ACP thread itself, so the
        # handle close must be skipped (self-join); the subprocess is already
        # gone. Still pops the registry entry, settles parked permissions, and
        # revokes the scoped token instead of leaving them to the TTL/timeout
        # backstops. Idempotent: a close-initiated teardown already popped the
        # runtime, making this a no-op.
        self._teardown_runtime(session_id, self._runtime(session_id), close_handle=False)
        current = self._db.get_studio_chat_session(session_id) or {}
        if current.get("status") in ("closed", "error"):
            return
        self._db.update_studio_chat_session_if(
            session_id,
            status_not_in=("closed", "error"),
            status="error",
            error_detail="agent process exited",
        )
        self._append_message(
            session_id, "status", "system", {"event": "error", "detail": "agent process exited"}
        )
        self._publish_session(session_id)
