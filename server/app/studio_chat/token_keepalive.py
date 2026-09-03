"""Mid-turn run-token keepalive + invalidation notice for studio chat (#411).

Turn-start renewal (send_message) cannot cover a turn that itself runs long:
the agent's MCP headers cannot be re-pointed mid-session, so once the token's
TTL lapses under a running turn every tool call 401s and the agent-side MCP
client eventually gives up ("Not connected") — while the chat UI, whose main
path never used the token, shows nothing at all. This module closes both
halves: keepalive (renew on each tool_call update, with a threshold wide
enough that a checked-live token always outlives the current turn) and
notice (one timeline message when the token is dead — death is detected on
the FIRST tool_call after it happens, mid-turn or in a later turn).

Cost design: the check rides the existing tool_call persistence path (one
indexed SELECT per tool_call, the same DB trip class as the tool_call INSERT
that follows it; the expiry UPDATE fires only when less than a turn-duration
of life remains). Called from the ACP notification path: everything here
must stay thread-safe and never raise into on_update.
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
# A checked-live token must outlive the current turn: the renew threshold is
# the turn-duration ceiling plus grace, NOT the turn-start 30min threshold —
# a token with 30-65 minutes left must still slide forward here, or a long
# turn can kill it after this check already reported it alive (#411 review).
_KEEPALIVE_RENEW_THRESHOLD = timedelta(seconds=PROMPT_TIMEOUT_SECONDS) + timedelta(minutes=5)


def _token_alive(backend: ServiceBackend, token: str) -> bool:
    """A dead token (revoked / expired / user disabled) no longer resolves to
    a user; a live one is slid forward — never revoking or reviving anything,
    the same leaked-token guarantees as turn-start renewal."""
    if backend.db.get_scoped_token_user(hash_token(token)) is None:
        return False
    renew_scoped_token(backend.db, token, threshold=_KEEPALIVE_RENEW_THRESHOLD)
    return True


def keepalive_run_token(backend: ServiceBackend, session_id: str) -> None:
    """Renew the session's run token on a tool_call update; notice once dead.

    Runs on EVERY tool_call update — a once-per-runtime check would go blind
    after the first one: token death is only ever detected after it happens
    (mid-turn expiry, idle-expiry before a later turn, admin revoke), so the
    check must keep firing for as long as the agent keeps calling tools. The
    per-runtime done-flag deduplicates only the DEAD notice (one timeline
    warning is enough; a resume mints a fresh runtime with a fresh token and
    its own flag). Both the check and the notice append are individually
    guarded: a transient DB failure logs a warning and leaves the flag unset
    so the next tool_call retries. Callers invoke this AFTER appending the
    tool_call row — the message is the durable record, this never raises.
    """
    runtime: SessionRuntime | None = backend.runtime(session_id)
    if runtime is None:
        return
    with runtime.lock:
        if runtime.token_keepalive_done:
            return
    alive: bool
    try:
        alive = _token_alive(backend, runtime.token)
    except Exception:
        # #204 broad-except audit: best-effort keepalive on the notification
        # path. The tool_call message is already persisted by the caller, so
        # a transient DB failure here must not propagate into it; the token
        # keeps its TTL as backstop and the next tool_call retries the check
        # (done-flag stays unset). The warning with traceback is the operator
        # signal.
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
        # — the notice is the whole point of this path, so a failed append
        # must retry on the next tool_call (flag set only on success below)
        # rather than being permanently lost, and must never break the
        # notification path around it.
        logger.warning(
            "studio chat run_token_invalidated notice failed for %s", session_id, exc_info=True
        )
        return
    with runtime.lock:
        runtime.token_keepalive_done = True
