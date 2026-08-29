"""Live (in-memory) per-session runtime state containers (studio chat).

Split from service.py (file budget): the runtime containers; the single
teardown path (deny pending permissions, stop the subprocess, revoke the
scoped token) lives in teardown.py.
"""

from __future__ import annotations

import threading
from typing import Any

from server.app.studio_chat.acp_session import AcpSessionHandle
from server.app.studio_chat.streaming import TurnStreamState


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
        # Set by resume_session when the fresh agent could not reload the
        # prior ACP session: the first post-resume prompt gets the persisted
        # transcript prepended (consumed and cleared in send_message).
        self.resume_transcript_pending = False
        # Whether the one-time advisory mcp_unverified hint was already
        # posted for this session (per-session, not per-turn).
        self.mcp_hint_shown = False
