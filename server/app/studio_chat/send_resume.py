"""Send-path seamless resume (#1121): a dead run token rebuilds the runtime
BEFORE the message is admitted.

Session layer "no expiry": a studio chat session that sits idle past the run
token's TTL used to answer the next send with 409 + 「会话已中断」 and drop the
typed message. send_message now checks token liveness up front; a dead token
on an idle/error session reroutes through the same claim/teardown/spawn state
machine as the manual 「继续对话」 resume (studio_chat.resume), and the message
is admitted onto the FRESH runtime — a message is only persisted once the
session can actually answer it. Only a resume failure leaves the session in
error with the message unsent (the manual resume entry stays for that case).

Credential-layer semantics are untouched: the leaked-token fuse (TTL), the
no-revival rule (the dead token stays dead — resume mints a NEW one), the
scope narrowing, and close-revocation all live where they always did. The
「工具通道已失效」 timeline notice is NOT appended on this path: recovery
follows within the same request, so the session_resumed row is the trace.

Concurrency: a per-session lock (service._send_resume_locks) serializes
auto-resume attempts — the loser of a concurrent attempt re-checks under the
lock and rides the winner's fresh runtime instead of spawning twice. The
closed/error -> starting claim (claim_studio_chat_resume) stays the durable
once-only guard against resumes that bypass the lock (the manual endpoint):
a claim conflict with a live fresh runtime is ridden, not re-raised. The lock
map is keyed by session id, never pruned — entries are tiny and bounded by
the number of sessions ever auto-resumed in this process.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from server.app.auth.sessions import hash_token
from server.app.services.job_errors import ConflictError
from server.app.studio_chat.session_escalation import escalate_dead_token_session
from server.app.studio_chat.token_keepalive import TOKEN_INVALIDATED_DETAIL

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService

# Auto-resume only fires for sessions that CAN accept a message right away:
# an idle row claims the turn directly, an error row is what the keepalive /
# admission escalation lands a dead-token session on. A running turn keeps the
# existing fail-closed path (require_live_run_token escalates + 409): ripping
# down an in-flight turn would lose partial work the agent may still deliver.
_AUTO_RESUMABLE_STATUSES = ("idle", "error")


def run_token_alive(service: StudioChatService, runtime: SessionRuntime) -> bool:
    """Liveness SELECT without the renew slide (dead = revoked/expired/user
    disabled — get_scoped_token_user resolves none of them)."""
    return service.db.get_scoped_token_user(hash_token(runtime.token)) is not None


def needs_send_resume(
    service: StudioChatService, session: dict[str, Any], runtime: SessionRuntime | None
) -> bool:
    """Cheap pre-lock gate on the caller's snapshot; ensure_answerable_runtime
    re-decides under the lock on a fresh read."""
    return (
        runtime is not None
        and session["status"] in _AUTO_RESUMABLE_STATUSES
        and not run_token_alive(service, runtime)
    )


def ensure_answerable_runtime(
    service: StudioChatService, session_id: str, workspace_id: str
) -> tuple[dict[str, Any], SessionRuntime]:
    """Return a (session, runtime) pair whose run token is live, resuming once
    when the current one is dead. Raises (message never persisted) when the
    resume fails or cannot yield a live channel."""
    with _resume_lock(service, session_id):
        session = service.get_session(session_id, workspace_id)
        runtime = service.runtime(session_id)
        if runtime is not None and run_token_alive(service, runtime):
            # A concurrent resume (or a renewal landing first) already
            # restored the channel — ride it, never spawn a second one.
            return session, runtime
        if runtime is None:
            # Torn down between the caller's snapshot and the lock: refuse;
            # the retry's top-level orphan projection owns the error stamp.
            raise ConflictError("Chat session is not running on this server")
        if session["status"] not in _AUTO_RESUMABLE_STATUSES:
            # Raced into a non-resumable shape (close, a turn claim, a manual
            # resume still spawning): hand back to the normal admission path,
            # whose fail-closed token check owns the refusal.
            return session, runtime
        # The resume claim only accepts closed/error rows: escalate the
        # dead-token row first (guarded write — a concurrent close/claim owns
        # its final state and no-ops this). No timeline notice here.
        escalate_dead_token_session(service, session_id)
        try:
            service.resume_session(session_id, workspace_id, str(session["user_id"]))
        except ConflictError:
            # A lock-bypassing resume (manual 「继续对话」) won the claim: ride
            # its fresh runtime; any other conflict (cap, race loss with no
            # live successor) stays a refusal.
            raced = service.runtime(session_id)
            if raced is not None and run_token_alive(service, raced):
                return service.get_session(session_id, workspace_id), raced
            raise
        runtime = service.runtime(session_id)
        if runtime is None or not run_token_alive(service, runtime):
            # Resume returned without a live channel (e.g. a concurrent close
            # fenced the spawn): refuse rather than admit an unanswerable
            # message.
            raise ConflictError(TOKEN_INVALIDATED_DETAIL)
        return service.get_session(session_id, workspace_id), runtime


def _resume_lock(service: StudioChatService, session_id: str) -> threading.Lock:
    with service._runtimes_lock:
        return service._send_resume_locks.setdefault(session_id, threading.Lock())
