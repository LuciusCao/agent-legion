"""MCP-visibility advisory hint for Studio chat sessions (split from service.py).

The behavioural smoke signal answers one question: has this session ever shown
an agent-legion MCP tool call? A turn without one is NOT evidence of a wiring
problem — the user may have asked a pure Q&A question, or cancelled a misfired
submit mid-run. The hint is therefore advisory, surfaced at most once per
session, and never on cancelled turns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.studio_chat.prompts import looks_like_agent_legion_tool_call

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend


def is_agent_legion_tool_call(payload: dict[str, Any]) -> bool:
    # Match only the structured identity fields (title/kind/name). Never
    # serialize the whole payload: rawInput carries the agent's local
    # command text (e.g. a Bash line mentioning a platform tool name),
    # which must not win an MCP auto-approve.
    fields = (payload.get(key) for key in ("title", "kind", "name"))
    return any(
        looks_like_agent_legion_tool_call(value) for value in fields if isinstance(value, str)
    )


MCP_UNVERIFIED_HINT = (
    "本会话还没有任何 agent-legion 平台工具调用的迹象；如果你期望"
    " agent 读写平台状态，请检查其 MCP 配置。纯问答类对话可忽略本提示。"
)


def maybe_emit_mcp_hint(backend: ServiceBackend, session_id: str, stop_reason: str) -> None:
    """Emit the one-time mcp_unverified hint when applicable.

    Conditions: the runtime never observed an agent-legion tool call, the
    session is not already verified, this turn actually completed (a
    user-cancelled turn is not evidence of anything), and the hint has not
    been shown before in this session. The shown-once memory is the
    persisted mcp_status='unverified' itself, so the guarantee survives
    backend restarts and runtime rebuilds; the in-memory flag only
    deduplicates within one runtime.
    """
    session = backend.db.get_studio_chat_session(session_id) or {}
    runtime = backend.runtime(session_id)
    mcp_observed = runtime.mcp_observed if runtime is not None else False
    if (
        mcp_observed
        or session.get("mcp_status") in ("verified", "unverified")
        or stop_reason == "cancelled"
        or (runtime is not None and runtime.mcp_hint_shown)
    ):
        return
    if runtime is not None:
        with runtime.lock:
            runtime.mcp_hint_shown = True
    backend.db.update_studio_chat_session(session_id, mcp_status="unverified")
    backend.store.append_message(
        session_id,
        "status",
        "system",
        {"event": "mcp_unverified", "detail": MCP_UNVERIFIED_HINT},
    )
