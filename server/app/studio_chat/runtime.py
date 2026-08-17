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
        # Streaming chunk coalescing (agent text + thought): each kind folds
        # into one message row per turn; the slots are reset at turn START
        # (send_message), so trailing chunks of a finished turn still fold
        # into that turn's rows (#98).
        self.stream = TurnStreamState()
        self.mcp_observed = False


def teardown_runtime(
    db: JobQueries,
    runtimes: dict[str, SessionRuntime],
    runtimes_lock: threading.Lock,
    session_id: str,
    runtime: SessionRuntime | None,
) -> None:
    """Deny pending permissions, stop the subprocess, revoke the token."""
    with runtimes_lock:
        current = runtimes.pop(session_id, None)
    runtime = current if current is not None else runtime
    if runtime is None:
        return
    with runtime.lock:
        for pending in runtime.pending_permissions.values():
            pending.decision = {"deny": True}
            pending.event.set()
    runtime.handle.close()
    try:
        revoke_scoped_token(db, runtime.token)
    except Exception:
        logger.warning("failed to revoke studio chat token for %s", session_id)
