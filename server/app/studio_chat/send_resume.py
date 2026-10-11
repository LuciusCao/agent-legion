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
claim moves idle/error -> starting in ONE capacity-checked statement (#1238
codex round-4 P1: the row never transits an error state that drops out of the
capacity count while its runtime still lives, and a close landing before the
claim makes it match zero rows — the row stays closed; round-1 P1's generational
CAS was subsumed by this); the starting claim stays the durable once-only
guard against resumes that bypass the lock (the manual endpoint): a claim
conflict with a live fresh runtime is ridden, not re-raised. The lock map is
keyed by session id, never pruned — entries are tiny and bounded by the
number of sessions ever auto-resumed in this process.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from server.app.auth.sessions import hash_token
from server.app.services.job_errors import ConflictError
from server.app.studio_chat import resume as resume_module
from server.app.studio_chat.resume_preflight import require_resumable_agent
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


def ensure_answerable_runtime(
    service: StudioChatService, session_id: str, workspace_id: str, user_id: str | None = None
) -> tuple[dict[str, Any], SessionRuntime | None]:
    """Re-read under the per-session resume lock (which waits out an in-flight
    auto-resume, #1121 codex P2-2) and return a (session, runtime) pair whose
    run token is live, resuming once when the current one is dead. A runtime
    still absent after the wait is a real orphan — returned as None for the
    caller's orphan projection. A session that raced into a non-resumable
    status falls through unchanged — the normal admission path owns its
    refusal. Raises (message never persisted) when the resume fails or cannot
    yield a live channel. The resume mints the fresh token for ``user_id``
    (the current sender; #1121 codex P2-1 — the session creator may have been
    disabled since, which is precisely why the old token reads dead); internal
    callers without an authenticated sender fall back to the creator."""
    with _resume_lock(service, session_id):
        session = service.get_session(session_id, workspace_id)
        runtime = service.runtime(session_id)
        if runtime is not None and run_token_alive(service, runtime):
            # A concurrent resume (or a renewal landing first) already
            # restored the channel — ride it, never spawn a second one.
            return session, runtime
        if runtime is None:
            # #1121 codex P2-2 (round 3): arriving here means an in-flight
            # auto-resume held this lock (old runtime torn down, new one not
            # yet registered) — the lock wait above already rode it out, so a
            # still-absent runtime means a real orphan: hand back and let the
            # caller's orphan projection own the stamp and the structured 409.
            return session, None
        if session["status"] not in _AUTO_RESUMABLE_STATUSES:
            # Raced into a non-resumable shape (close, a turn claim, a manual
            # resume still spawning): hand back to the normal admission path,
            # whose fail-closed token check owns the refusal.
            return session, runtime
        # #1238 codex round 4: everything fallible prepares BEFORE the first
        # write — and its result rides the whole chain (the agent resolved
        # here is NOT re-resolved inside resume). The claim then moves
        # idle/error -> starting in ONE capacity-checked statement (P1: no
        # intermediate error state that drops the row out of the capacity
        # count while its runtime still lives; a close that lands before the
        # claim simply makes it match zero rows — the row stays closed).
        agent = require_resumable_agent(service, session)
        try:
            # The lifecycle admission of the decorated service.resume_session,
            # without its closed-accepting claim.
            with service._lifecycle.starting():
                resume_module.resume_session(
                    service,
                    session_id,
                    workspace_id,
                    user_id or str(session["user_id"]),
                    from_statuses=_AUTO_RESUMABLE_STATUSES,
                    agent=agent,
                )
        except ConflictError:
            # A lock-bypassing resume (manual 「继续对话」) won the claim: ride
            # its fresh runtime; a row that raced to closed stays closed; a
            # cap-full claim wrote nothing (the row keeps its truthful idle/
            # error state and the old runtime keeps living — capacity
            # accounting untouched); any other conflict stays a refusal.
            raced = service.runtime(session_id)
            if raced is not None and run_token_alive(service, raced):
                return service.get_session(session_id, workspace_id), raced
            if (service.db.get_studio_chat_session(session_id) or {}).get("status") == "closed":
                raise ConflictError("Chat session is closed") from None
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
