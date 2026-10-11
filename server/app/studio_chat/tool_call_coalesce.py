"""In-place coalescing of tool_call / tool_call_update frames (#1120).

Every ACP tool frame used to append a new message row carrying a full
rawInput/rawOutput snapshot: N updates per call meant N rows and N frontend
copies, and the 500-row history window filled with tool-update fragments.
Like stream text chunks (``store.append_stream_chunk``), a call's frames now
share ONE row: the first frame inserts it; each later frame shallow-merges
into it (``{**old, **new}`` — an update carrying only status/rawOutput keeps
the first frame's title/kind/rawInput) and publishes a no-seq half-row SSE
frame the frontend folds in by id (``upsertMessage``). seq identity and
created_at never move; schema untouched.

Ownership is the in-memory ``runtime.tool_call_messages`` map, so a resume
or restart starts empty and an update for a pre-restart toolCallId degrades
to a fresh row — a behavioural fallback, never data loss. Entries drop at
terminal status, bounding the map to in-flight calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.studio_chat.streaming import tool_call_message_payload

if TYPE_CHECKING:
    from server.app.jobs import JobQueries
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.store import StudioChatStore

# Same terminal set as tool_call_commands._FINISHED (ACP tool call statuses).
_TERMINAL_STATUSES = frozenset({"completed", "failed"})


def coalesce_tool_call(
    db: JobQueries,
    store: StudioChatStore,
    session_id: str,
    runtime: SessionRuntime,
    update: dict[str, Any],
) -> dict[str, Any] | None:
    """Persist one tool frame; returns the inserted row, None for a merge."""
    tool_call_id = update.get("toolCallId")
    if not isinstance(tool_call_id, str) or not tool_call_id:
        return store.append_message(session_id, "tool_call", "agent", update)
    with runtime.lock:
        open_id = runtime.tool_call_messages.get(tool_call_id)
        merged = (
            db.merge_studio_chat_message_content(open_id, update) if open_id is not None else None
        )
        if open_id is None or merged is None:
            # Insert path: the call's first frame, a toolCallId predating this
            # runtime (the map is in-memory only — the update still lands, as
            # its own row), or a vanished row. A terminal first frame is not
            # tracked: no further updates are due, so the map stays bounded.
            message = store.append_message(session_id, "tool_call", "agent", update)
            if str(update.get("status") or "") not in _TERMINAL_STATUSES:
                runtime.tool_call_messages[tool_call_id] = message["id"]
            return message
        if str(update.get("status") or "") in _TERMINAL_STATUSES:
            runtime.tool_call_messages.pop(tool_call_id, None)
    payload = tool_call_message_payload(session_id, open_id, merged)
    store.publish(session_id, {"type": "message", "message": payload}, replaceable=True)
    return None
