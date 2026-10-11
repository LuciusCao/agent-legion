"""Resume preflight (agent catalog + host availability), shared by the manual
「继续对话」 resume and the send-path auto-resume (#1121).

Both checks are fail-fast and side-effect-free. The send path runs them BEFORE
stamping idle -> error (codex P2-1, round 3 — multi-step changes prepare
everything fallible first, then apply): a session whose agent was deleted or
whose command no longer runs on this host keeps its truthful idle row (the old
runtime is still alive), the send raises the preflight's explanatory error,
and re-sending IS the retry entry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.services.job_errors import InvalidOperationError

if TYPE_CHECKING:
    from server.app.studio_chat.service import StudioChatService


def require_resumable_agent(service: StudioChatService, session: dict[str, Any]) -> dict[str, Any]:
    """Resolve the session's agent and verify its command runs on this host."""
    agent_id = str(session["agent_id"])
    agent = service._registry.find_agent(agent_id)
    if agent is None:
        raise InvalidOperationError(f"Unknown studio agent: {agent_id}")
    command = str(agent["command"])
    if not service._probe.available(command):
        raise InvalidOperationError(
            f"Studio agent '{agent_id}' is not available on this host"
            f" (command not found: {command})"
        )
    return agent
