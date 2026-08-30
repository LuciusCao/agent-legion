"""The single runtime-teardown path for studio chat sessions.

Split from runtime.py (file budget): close/shutdown/failed-start/agent-death
all funnel through teardown_runtime — deny pending permissions, stop the
subprocess, revoke the scoped token. The runtime containers themselves stay
in runtime.py.
"""

from __future__ import annotations

import logging
import threading

from server.app.auth.scoped_tokens import revoke_scoped_token
from server.app.jobs import JobQueries
from server.app.studio_chat.runtime import SessionRuntime

logger = logging.getLogger(__name__)


def teardown_runtime(
    db: JobQueries,
    runtimes: dict[str, SessionRuntime],
    runtimes_lock: threading.Lock,
    session_id: str,
    runtime: SessionRuntime | None,
    *,
    close_handle: bool = True,
    expected: SessionRuntime | None = None,
) -> bool:
    """Deny pending permissions, stop the subprocess, revoke the token.

    close_handle=False is for the agent-death path (#158): _on_exit runs on
    the ACP thread itself, where handle.close()'s thread join would deadlock
    (self-join) — the subprocess is already gone, so only the registry entry,
    pending permissions, and the token need cleanup.

    expected pins the caller's own runtime identity (agent-death path): the
    registry entry is popped only when it still IS that runtime. Resume made
    a second runtime per session_id possible — a stale death echo from the
    old thread firing after resume registered the new runtime must not pop
    (and closed-flag/token-revoke) the NEW runtime (ABA); it tears down only
    its own. Returns whether the teardown owned (popped) the registry's live
    entry: a stale echo that found a newer runtime registered must not stamp
    the session row either.
    """
    with runtimes_lock:
        if expected is None:
            current = runtimes.pop(session_id, None)
            runtime = current if current is not None else runtime
            owned = current is not None
        else:
            owned = runtimes.get(session_id) is expected
            if owned:
                runtimes.pop(session_id)
            # The registry already holds a different (newer) runtime: tear
            # down only the caller's own, leave the registry untouched.
            runtime = expected
    if runtime is None:
        return owned
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
    return owned


def revoke_minted_token_quietly(db: JobQueries, token: str, session_id: str) -> None:
    """Best-effort revoke for a minted token whose handle never materialized
    (#204: must not mask the propagating startup failure; the token TTL plus
    the maintenance purge are the backstop, exc_info keeps root cause visible).
    """
    try:
        revoke_scoped_token(db, token)
    except Exception:
        # #204 broad-except audit: same teardown safety-net semantics as
        # teardown_runtime above — the startup failure that brought us here
        # is already propagating and must stay the signal the caller sees.
        logger.warning("failed to revoke studio chat token for %s", session_id, exc_info=True)
