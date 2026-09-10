"""Studio-chat session status escalation for unrecoverable tool channels (#558).

A dead run token kills the agent's MCP tool channel permanently (the token
rides the agent's MCP headers, which cannot be re-pointed mid-session), but
the chat main path — ACP process, prompt loop, timeline — stays healthy, so
the session row sat at "idle" forever: the UI showed a live session whose
every tool call failed, with no recovery entry (resume only accepts
closed/error rows). Escalating to "error" makes the existing ResumeBar /
「继续对话」 flow reachable: resume mints a fresh token and rebuilds the
channel with the session's context preserved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend

# Bounded like on_error's 500-char error_detail ceiling.
_TOKEN_DEAD_ERROR_DETAIL = "run token invalidated: tool channel unrecoverable (#558)"


def escalate_dead_token_session(backend: ServiceBackend, session_id: str) -> None:
    """Move a dead-tool-channel session to error (the resume-reachable state).

    Guarded like on_error's fatal arm: a concurrent close owns the final
    'closed' state, and a mid-flight resume claim's 'starting' row is not
    ours to stamp; 'error' is already terminal for this purpose. The running
    turn's own turn_end (status_in running/awaiting_permission → idle)
    cannot resurrect an escalated row. Runtime teardown deliberately stays
    with resume/on_exit — the ACP process itself is healthy and the current
    turn is allowed to finish."""
    backend.db.update_studio_chat_session_if(
        session_id,
        status_not_in=("closed", "error", "starting"),
        status="error",
        error_detail=_TOKEN_DEAD_ERROR_DETAIL,
    )
    backend.store.publish_session(session_id)
