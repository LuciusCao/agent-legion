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

The header alone is only a claim: any holder of a studio-agent token could
otherwise point a same-workspace save at another session's timeline (codex
P2). The event is therefore written only when ALL of these hold:

- the presented Bearer token IS the named session's run token — the exact
  token minted at spawn and held by the in-process ``SessionRuntime``
  (a resume mints a fresh token + runtime, so a pre-resume token cannot
  write into the resumed session; tokens without a runtime binding — e.g.
  self-service origin='user' tokens — conservatively get no card);
- the session exists and belongs to the route's workspace;
- the session is still live — the liveness predicate and the INSERT are one
  statement (``append_message_if_live``), so a close/delete racing the save
  gets no card.

A forged, stale, or foreign header degrades to "no card", never to a failed
save: the card is a best-effort notification layered on the committed draft.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any, Protocol

from server.app.jobs import JobQueries
from server.app.studio_chat.runtime import SessionRuntime
from server.app.studio_chat.store import StudioChatStore

logger = logging.getLogger(__name__)

DRAFT_MESSAGE_KIND = "draft"
DRAFT_SAVED_EVENT = "draft_saved"
# Same live set as callback_check: a save landing after close/archive/error
# gets no card (the session no longer renders an actionable timeline).
_LIVE_STATUSES = ("starting", "idle", "running", "awaiting_permission")


class DraftCardBackend(Protocol):
    """What the emit path needs from the chat service (structural subset of
    StudioChatService; the routes take the service itself or None)."""

    @property
    def db(self) -> JobQueries: ...

    @property
    def store(self) -> StudioChatStore: ...

    def runtime(self, session_id: str) -> SessionRuntime | None: ...


def _append(
    backend: DraftCardBackend,
    session_id: str,
    workspace_id: str,
    presented_token: str | None,
    content: dict[str, Any],
) -> None:
    try:
        runtime = backend.runtime(session_id)
        if (
            runtime is None
            or not presented_token
            or not hmac.compare_digest(runtime.token, presented_token)
        ):
            return
        session = backend.db.get_studio_chat_session(session_id)
        if session is None or str(session["workspace_id"]) != str(workspace_id):
            return
        backend.store.append_message_if_live(
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
    backend: DraftCardBackend | None,
    session_id: str | None,
    presented_token: str | None,
    workspace_id: str,
    node_key: str,
    row: dict[str, Any],
) -> None:
    """Success-path hook of the node-code draft save route (row = save response)."""
    if backend is None or not session_id:
        return
    _append(
        backend,
        session_id,
        workspace_id,
        presented_token,
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
    backend: DraftCardBackend | None,
    session_id: str | None,
    presented_token: str | None,
    workspace_id: str,
    draft: dict[str, Any],
) -> None:
    """Success-path hook of the workflow draft CAS save route (draft = saved row)."""
    if backend is None or not session_id:
        return
    _append(
        backend,
        session_id,
        workspace_id,
        presented_token,
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
