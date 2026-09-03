"""Mid-turn run-token keepalive + invalidation notice for studio chat (#411).

Turn-start renewal (send_message) cannot cover a turn that itself runs long:
the agent's MCP headers cannot be re-pointed mid-session, so once the token's
TTL lapses under a running turn every tool call 401s and the agent-side MCP
client eventually gives up ("Not connected") — while the chat UI, whose main
path never used the token, shows nothing at all. This module closes both
halves: keepalive (renew on each `tool_call` sessionUpdate — status-only
`tool_call_update` excluded — threshold wide enough that a checked-live
token always outlives the current turn) and notice (one timeline message
when the token is dead, detected on the first tool_call after it happens).
Cost per tool_call: one liveness SELECT, one conditional UPDATE (no-row when
plenty of life remains; matching slides at most once per ~55min), plus a
re-check SELECT only in the no-row case. ACP notification path, never raises.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from server.app.auth.scoped_tokens import renew_scoped_token
from server.app.auth.sessions import hash_token
from server.app.studio_chat.acp_session import PROMPT_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend
    from server.app.studio_chat.runtime import SessionRuntime

logger = logging.getLogger(__name__)

TOKEN_INVALIDATED_DETAIL = (
    "工具通道已失效（运行凭证过期或被吊销），agent 暂时无法调用平台工具；"
    "关闭当前会话后点「继续对话」重建即可恢复。"
)
# A checked-live token must outlive the current turn: the threshold is the
# turn-duration ceiling plus grace, NOT the turn-start 30min one (#411 review).
_KEEPALIVE_RENEW_THRESHOLD = timedelta(seconds=PROMPT_TIMEOUT_SECONDS + 300)


def _token_alive(backend: ServiceBackend, token: str) -> bool:
    """Dead (revoked / expired / user disabled) tokens no longer resolve to a
    user; a live one is slid forward, never revoked or revived — the same
    leaked-token guarantees as turn-start renewal. The slide's rowcount
    closes the check→update race: dying between the SELECT and the UPDATE
    matches zero rows, and one re-check tells "no slide needed" apart from
    "died under us" (#411 review)."""
    token_hash = hash_token(token)
    if backend.db.get_scoped_token_user(token_hash) is None:
        return False
    return renew_scoped_token(backend.db, token, threshold=_KEEPALIVE_RENEW_THRESHOLD) or (
        backend.db.get_scoped_token_user(token_hash) is not None
    )


def keepalive_run_token(backend: ServiceBackend, session_id: str) -> None:
    """Renew the session's run token on a `tool_call` update; notice once dead.

    Runs on EVERY tool_call — a once-per-runtime check would go blind after
    the first one: token death is only ever detected after it happens
    (mid-turn expiry, idle-expiry before a later turn, admin revoke), so the
    check must keep firing while the agent keeps calling tools. The done-flag
    deduplicates only the DEAD notice (a resume mints a fresh runtime, token,
    and flag). Check and notice append are guarded — a transient DB failure
    leaves the flag unset so the next tool_call retries. Callers run this
    AFTER the tool_call row append.
    """
    runtime: SessionRuntime | None = backend.runtime(session_id)
    if runtime is None:
        return
    with runtime.lock:
        if runtime.token_keepalive_done:
            return
    try:
        alive = _token_alive(backend, runtime.token)
    except Exception:
        # #204 broad-except audit: best-effort keepalive on the notification
        # path. The tool_call message is already persisted by the caller, so
        # a transient DB failure must not propagate into it; the TTL is the
        # backstop and the next tool_call retries (flag stays unset).
        logger.warning("studio chat token keepalive check failed for %s", session_id, exc_info=True)
        return
    if alive:
        return
    try:
        backend.store.append_message(
            session_id,
            "status",
            "system",
            {"event": "run_token_invalidated", "detail": TOKEN_INVALIDATED_DETAIL},
        )
    except Exception:
        # #204 broad-except audit: same swallow semantics as the check above
        # — a failed append must retry on the next tool_call (flag set only
        # on success below) rather than be permanently lost, and must never
        # break the notification path around it.
        logger.warning(
            "studio chat run_token_invalidated notice failed for %s", session_id, exc_info=True
        )
        return
    with runtime.lock:
        runtime.token_keepalive_done = True
