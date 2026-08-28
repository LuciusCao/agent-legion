"""Live (in-memory) per-session runtime state and teardown (studio chat).

Split from service.py (file budget): the runtime containers plus the single
teardown path (deny pending permissions, stop the subprocess, revoke the
scoped token) that close/shutdown/failed-start all funnel through.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from server.app.auth.scoped_tokens import revoke_scoped_token
from server.app.jobs import JobQueries
from server.app.studio_chat.acp_session import AcpSessionHandle
from server.app.studio_chat.streaming import TurnStreamState

logger = logging.getLogger(__name__)


class PendingPermission:
    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.event = threading.Event()
        self.decision: dict[str, Any] = {"deny": True}


class SessionRuntime:
    """Live (in-memory) state for one chat session; never persisted."""

    def __init__(self, handle: AcpSessionHandle, token: str) -> None:
        self.handle = handle
        # Raw scoped token, held only to revoke on close; never leaves memory.
        self.token = token
        self.lock = threading.Lock()
        self.pending_permissions: dict[str, PendingPermission] = {}
        # Set under lock by teardown before the pending-permission settle
        # sweep: a permission request that parks afterwards (it takes the same
        # lock) denies immediately instead of hanging to the timeout (#158).
        self.closed = False
        # Streaming chunk coalescing (agent text + thought): each kind folds
        # into one message row per turn; the slots are reset at turn START
        # (send_message), so trailing chunks of a finished turn still fold
        # into that turn's rows (#98).
        self.stream = TurnStreamState()
        self.mcp_observed = False
        # Whether the one-time advisory mcp_unverified hint was already
        # posted for this session (per-session, not per-turn).
        self.mcp_hint_shown = False


def teardown_runtime(
    db: JobQueries,
    runtimes: dict[str, SessionRuntime],
    runtimes_lock: threading.Lock,
    session_id: str,
    runtime: SessionRuntime | None,
    *,
    close_handle: bool = True,
) -> None:
    """Deny pending permissions, stop the subprocess, revoke the token.

    close_handle=False is for the agent-death path (#158): _on_exit runs on
    the ACP thread itself, where handle.close()'s thread join would deadlock
    (self-join) — the subprocess is already gone, so only the registry entry,
    pending permissions, and the token need cleanup.
    """
    with runtimes_lock:
        current = runtimes.pop(session_id, None)
    runtime = current if current is not None else runtime
    if runtime is None:
        return
    with runtime.lock:
        runtime.closed = True
        # Pop each waiter so a respond racing the settle finds the request
        # gone (dict membership is the not-yet-settled criterion, #158).
        while runtime.pending_permissions:
            _request_id, pending = runtime.pending_permissions.popitem()
            pending.decision = {"deny": True}
            pending.event.set()
    if close_handle:
        runtime.handle.close()
    try:
        revoke_scoped_token(db, runtime.token)
    except Exception:
        # #204 broad-except audit: teardown safety net. The session is being
        # discarded either way — the registry entry is already popped and the
        # subprocess is already being closed above — so the revoke failing
        # must not mask whatever the caller is propagating (startup failure,
        # shutdown loop). Scoped tokens carry their own TTL, and expired
        # tokens are purged by the workflow maintenance sweep, so a leaked
        # revoke self-heals eventually; the warning is the operator signal.
        logger.warning("failed to revoke studio chat token for %s", session_id)
