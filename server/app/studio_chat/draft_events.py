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

## Write-path model (codex #1240 P2-a/b/c)

The event write is ONE critical section with three gates, ordered so the
timeline can never contradict the authoritative draft:

1. **Runtime generation (P2-a)** — under the service's runtime-registry
   lock (``_runtimes_lock``, the same lock close/resume/archive swap
   generations under; session_close.py sets the DB-write-inside precedent):
   the presented Bearer token must equal the CURRENT runtime's run token.
   A close/resume that swaps the registry entry mid-flight blocks until the
   write is done, so a stale (pre-swap) token can never card the new
   generation's timeline. We never take ``runtime.lock`` inside (lock order
   elsewhere is runtime.lock → _runtimes_lock).
2. **Session state (P2-c)** — one INSERT statement (jobs/queries/
   studio_chat_draft_events.py) re-asserts id + workspace + live status +
   not deleted + NOT ARCHIVED: archive stamps archived_at before the close
   lands, so the stamp alone fences the write.
3. **Draft currency (P2-b)** — the same statement asserts the announced
   draft is still current (node_code: the in-place-overwritten draft row is
   still a draft with this code_hash; workflow: the draft row's updated_at
   is still this save's). A stalled writer's event is dropped instead of
   out-ordering the save that overwrote it, so the landed event order IS
   the authoritative draft-write order the frontend merges by.

Tokens without a runtime binding (self-service origin='user') get no card —
the binding cannot be verified, so the write is refused conservatively.
A forged, stale, or foreign header degrades to "no card", never to a failed
save: the card is a best-effort notification layered on the committed draft.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from server.app.studio_chat.payloads import serialize_message

if TYPE_CHECKING:
    from server.app.studio_chat.service import StudioChatService

logger = logging.getLogger(__name__)

DRAFT_MESSAGE_KIND = "draft"
# Same live set as callback_check: a save landing after close/archive/error
# gets no card (the session no longer renders an actionable timeline).
_LIVE_STATUSES = ("starting", "idle", "running", "awaiting_permission")


def _content(draft_type: str, **fields: Any) -> dict[str, Any]:
    return {"event": "draft_saved", "draft_type": draft_type, **fields}


def _append(
    service: StudioChatService,
    session_id: str,
    presented_token: str | None,
    insert: Callable[[], dict[str, Any] | None],
) -> None:
    """Token binding check + guarded insert in one registry-lock section.

    ``insert`` runs INSIDE the lock and performs the session-state and
    draft-currency gates in a single statement (None = gated out). The bus
    publish is non-blocking (store.py maintenance constraint), so the
    registry lock stays a leaf.

    Accepted cost (#1240 复验 P3): ``_runtimes_lock`` is process-global, so
    the guarded INSERT's DB round-trip queues every ``service.runtime()``
    lookup (ACP callbacks / admission / teardown / close) process-wide — a
    slow DB widens the throttle from one session to all studio chat
    sessions. Accepted because the lock stays a leaf (no other lock is
    taken inside), every blocking source self-heals (the round-trip
    returns; the broad-except below bounds even a hung path at the request
    level), and correctness is unaffected — same precedent as
    session_close.py's in-lock DB write.
    """
    try:
        with service._runtimes_lock:
            runtime = service._runtimes.get(session_id)
            if runtime is None or not presented_token:
                return
            if not hmac.compare_digest(runtime.token, presented_token):
                return
            message = insert()
            if message is not None:
                service.store.publish(
                    session_id, {"type": "message", "message": serialize_message(message)}
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
    service: StudioChatService | None,
    session_id: str | None,
    presented_token: str | None,
    workspace_id: str,
    node_key: str,
    row: dict[str, Any],
) -> None:
    """Success-path hook of the node-code draft save route (row = save response)."""
    if service is None or not session_id:
        return
    # code_hash doubles as the card's publish CAS identity (#692); the
    # currency gate pins row id + hash — save_draft overwrites the draft row
    # IN PLACE, so the pair identifies exactly this save's content.
    code_hash = str(row["code_hash"])
    _append(
        service,
        session_id,
        presented_token,
        lambda: service.db.append_studio_chat_node_code_draft_event(
            session_id,
            workspace_id,
            _LIVE_STATUSES,
            _content("node_code", node_key=node_key, code_hash=code_hash),
            draft_row_id=str(row["id"]),
            code_hash=code_hash,
        ),
    )


def emit_workflow_draft_saved(
    service: StudioChatService | None,
    session_id: str | None,
    presented_token: str | None,
    workspace_id: str,
    draft: dict[str, Any],
) -> None:
    """Success-path hook of the workflow draft CAS save route (draft = saved row)."""
    if service is None or not session_id:
        return
    _append(
        service,
        session_id,
        presented_token,
        # Currency gate: the single-row draft's updated_at is this save's
        # generation marker (the CAS token shape). The card's「应用到编辑器」
        # acts on definition_yaml; definition_hash feeds the stale hint (#1143).
        lambda: service.db.append_studio_chat_workflow_draft_event(
            session_id,
            workspace_id,
            _LIVE_STATUSES,
            _content(
                "workflow",
                definition_yaml=draft["definition_yaml"],
                definition_hash=draft.get("definition_hash"),
            ),
            draft_updated_at=draft["updated_at"],
        ),
    )
