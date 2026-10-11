"""Synthetic draft-saved timeline events for the studio-agent save tools (#1123).

kimi does not forward a subagent's (Agent/AgentSwarm) internal MCP tool calls
as ACP ``session/update`` notifications, so the ``tool_call`` messages the
chat draft cards derive from (studioChatMessages.ts extractors) never land
for subagent saves — while the draft itself saves fine over the plain HTTP
loopback, which never touches the chat tables. The save routes therefore
append a synthetic ``kind='draft'`` message on the SUCCESS path whenever the
MCP loopback carried the session binding header
(``x-agent-legion-mcp-session-id``, forwarded by mcp_server/tool_client.py).
The frontend merges both evidence sources per entity and keeps the latest
seq, so the main-agent path (where the ACP tool_call messages DO arrive)
still shows exactly one card — the synthetic event only fills the subagent
gap.

The header is an attribution hint, not an authority: the event is appended
only when the session exists, belongs to the route's workspace (a scoped
token could otherwise point a same-workspace save at another session's
timeline), and is still live — the liveness predicate and the INSERT are one
statement (``append_message_if_live``), so a close/delete racing the save
simply gets no card. A forged, stale, or foreign header degrades to "no
card", never to a failed save.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.app.jobs import JobQueries
    from server.app.studio_chat.store import StudioChatStore

logger = logging.getLogger(__name__)

DRAFT_MESSAGE_KIND = "draft"
DRAFT_SAVED_EVENT = "draft_saved"
# Same live set as callback_check: a save landing after close/archive/error
# gets no card (the session no longer renders an actionable timeline).
_LIVE_STATUSES = ("starting", "idle", "running", "awaiting_permission")


def _append(
    store: StudioChatStore,
    db: JobQueries,
    session_id: str,
    workspace_id: str,
    content: dict[str, Any],
) -> None:
    try:
        session = db.get_studio_chat_session(session_id)
        if session is None or str(session["workspace_id"]) != str(workspace_id):
            return
        store.append_message_if_live(
            session_id, DRAFT_MESSAGE_KIND, "agent", content, _LIVE_STATUSES
        )
    except Exception:
        # #204 broad-except audit: the draft itself already committed — the
        # card is a best-effort notification layered on top. Failing the save
        # response here would tell the agent "not saved" while the draft
        # exists (the worst lie this path could tell), and the next save or
        # validate call re-offers the card. The failure modes (DB hiccup on
        # the session read/insert) leave the draft intact and are recoverable,
        # so warn-and-continue is the correct result space.
        logger.warning(
            "failed to append draft_saved message for session %s", session_id, exc_info=True
        )


def emit_node_code_draft_saved(
    store: StudioChatStore | None,
    db: JobQueries,
    session_id: str | None,
    workspace_id: str,
    node_key: str,
    row: dict[str, Any],
) -> None:
    """Success-path hook of the node-code draft save route (row = save response)."""
    if store is None or not session_id:
        return
    _append(
        store,
        db,
        session_id,
        workspace_id,
        {
            "event": DRAFT_SAVED_EVENT,
            "draft_type": "node_code",
            "node_key": node_key,
            # The publish CAS identity (#692): the card's publish entry is
            # gated on this hash, same as the tool-output-derived card.
            "code_hash": row.get("code_hash"),
        },
    )


def emit_workflow_draft_saved(
    store: StudioChatStore | None,
    db: JobQueries,
    session_id: str | None,
    workspace_id: str,
    draft: dict[str, Any],
) -> None:
    """Success-path hook of the workflow draft CAS save route (draft = saved row)."""
    if store is None or not session_id:
        return
    _append(
        store,
        db,
        session_id,
        workspace_id,
        {
            "event": DRAFT_SAVED_EVENT,
            "draft_type": "workflow",
            # The card's「应用到编辑器」acts on this yaml; the semantic
            # identity hash feeds the stale hint (#1143), same as the
            # validate/compare-derived card.
            "definition_yaml": draft["definition_yaml"],
            "definition_hash": draft.get("definition_hash"),
        },
    )
