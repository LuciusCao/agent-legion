"""Mid-turn run-token keepalive + invalidation notice for studio chat (#411/#558).

The agent's MCP headers cannot be re-pointed mid-session, so a token that
dies (mid-turn expiry, idle-expiry, admin revoke) kills the tool channel
while the chat main path stays healthy. This module keeps a live token alive
across long turns (renew on each `tool_call` sessionUpdate; threshold wide
enough that a checked-live token always outlives the turn) and, once dead,
escalates the session to error (resume-reachable — ResumeBar /「继续对话」
rebuilds the channel with a fresh token; #558, semantics in
session_escalation.py) then notices it on the timeline. ACP notification
path, never raises.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from server.app.auth.scoped_tokens import renew_scoped_token
from server.app.auth.sessions import hash_token
from server.app.studio_chat.prompt_turn import PROMPT_TIMEOUT_SECONDS
from server.app.studio_chat.session_escalation import escalate_dead_token_session

if TYPE_CHECKING:
    from server.app.studio_chat.events import ServiceBackend
    from server.app.studio_chat.runtime import SessionRuntime

logger = logging.getLogger(__name__)

TOKEN_INVALIDATED_DETAIL = (
    "工具通道已失效（运行凭证过期或被吊销），agent 暂时无法调用平台工具；"
    "点「继续对话」重建工具通道即可恢复，会话上下文将保留。"
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

    Runs on EVERY tool_call — token death is only ever detected after it
    happens. The done-flag deduplicates the DEAD path (a resume mints a fresh
    runtime, token, and flag). An inconclusive check may retry on the next
    tool_call; confirmed invalidation always requests stop even if its
    durable projection fails. Callers run this AFTER the tool_call row append."""
    runtime: SessionRuntime | None = backend.runtime(session_id)
    if runtime is None:
        return
    with runtime.lock:
        if (
            runtime.closed
            or backend.runtime(session_id) is not runtime
            or runtime.token_keepalive_done
        ):
            return
        _keepalive_locked(backend, session_id, runtime)


def _keepalive_locked(backend: ServiceBackend, session_id: str, runtime: SessionRuntime) -> None:
    """Keep authentication, escalation and stop on the same runtime generation."""
    try:
        alive = _token_alive(backend, runtime.token)
    except Exception:
        # #204 broad-except audit: best-effort keepalive on the notification
        # path. The tool_call message is already persisted by the caller, so
        # a transient DB failure must not propagate into it; the TTL is the
        # backstop and the next tool_call retries (flag stays unset).
        logger.warning("studio chat token keepalive check failed for %s", session_id, exc_info=True)
        return
    if alive or runtime.closed or backend.runtime(session_id) is not runtime:
        return
    invalidate_run_token(backend, session_id, runtime)


def invalidate_run_token(backend: ServiceBackend, session_id: str, runtime: SessionRuntime) -> None:
    """Consume known-dead evidence once; stopping never depends on notification I/O.

    Admission, keepalive and automatic delivery share this terminal path. A
    second liveness query cannot undo an already observed invalidation. The
    nonblocking stop lets on_exit reclaim the process even if escalation fails.
    """
    with runtime.lock:
        if runtime.closed or backend.runtime(session_id) is not runtime:
            return
        try:
            if not runtime.token_keepalive_done:
                escalate_dead_token_session(backend, session_id)
                backend.store.append_message(
                    session_id,
                    "status",
                    "system",
                    {"event": "run_token_invalidated", "detail": TOKEN_INVALIDATED_DETAIL},
                )
                runtime.token_keepalive_done = True
        except Exception:
            # #204 broad-except audit: known-dead credentials must stop even
            # when DB/notification I/O fails. on_exit reconciles the row; no
            # notice is marked delivered on failure. Preserve the cause.
            logger.warning(
                "studio chat token invalidation failed for %s", session_id, exc_info=True
            )
        finally:
            runtime.handle.request_stop()
