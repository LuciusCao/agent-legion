"""Closed/error session resume path (split from service.py for file budget).

The service keeps a thin ``resume_session`` delegate; everything after the
shutdown check lives here so the claim/teardown/spawn ordering stays in one
auditable place, mirroring how spawn.py owns the shared start path. Access to
the service's private collaborators matches the package idiom (callbacks.py
forwards into ``service._on_*``; tests monkeypatch ``service._registry``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.services.job_errors import ConflictError, InvalidOperationError
from server.app.studio_chat.callbacks import ServiceCallbacks
from server.app.studio_chat.spawn import spawn_session_runtime

if TYPE_CHECKING:
    from server.app.studio_chat.service import StudioChatService


def resume_session(
    service: StudioChatService, session_id: str, workspace_id: str, user_id: str
) -> dict[str, Any]:
    """Rebuild the runtime of a closed/error session; history is kept.

    The agent regains context two ways (session_load.open_acp_session):
    ACP session/load of the prior acp_session_id when the
    freshly-initialized agent advertises loadSession, otherwise a fresh
    ACP session plus the persisted transcript injected once into the
    first post-resume prompt (runtime.resume_transcript_pending).
    Resuming a session that is still live on this server is an idempotent
    no-op.
    """
    from server.app.studio_chat import service as service_module

    session = service.get_session(session_id, workspace_id)
    if session["status"] not in ("closed", "error"):
        if service.runtime(session_id) is not None:
            return session
        # Live status without a runtime must not be respawned blind: the
        # startup reaper owns that reconciliation (marks it error, after
        # which it becomes resumable).
        raise ConflictError(f"Chat session is {session['status']} and cannot be resumed")
    # A closed/error session can still have a registered runtime winding
    # down (fatal error -> _on_exit window) or left over by a reap that
    # raced a live handle: the resume winner settles it below, right
    # after the atomic claim.
    agent_id = str(session["agent_id"])
    agent = service._registry.find_agent(agent_id)
    if agent is None:
        raise InvalidOperationError(f"Unknown studio agent: {agent_id}")
    command = str(agent["command"])
    if not service._probe.available(command):
        raise InvalidOperationError(
            f"Studio agent '{agent_id}' is not available on this host"
            f" (command not found: {command})"
        )
    # Atomic closed/error -> starting claim under the creation cap's
    # advisory lock: a resume spawns a subprocess, so it counts against
    # the same active-session cap and cannot race creators past it. The
    # cap constant is read through the service module at call time so
    # tests monkeypatching it keep working.
    max_active = service_module.MAX_ACTIVE_STUDIO_CHAT_SESSIONS
    if not service.db.claim_studio_chat_resume(session_id, max_active=max_active):
        current = service.db.get_studio_chat_session(session_id) or {}
        if current.get("status") not in ("closed", "error"):
            raise ConflictError("Chat session was resumed concurrently")
        raise ConflictError(
            f"Too many active studio chat sessions (limit {max_active});"
            " close an existing session first"
        )
    # Winner-only teardown, strictly after the atomic claim and before
    # the spawn: teardown pops the registry's CURRENT entry (never the
    # caller's snapshot), so a pre-claim teardown let a resume that went
    # on to lose the claim destroy the winner's freshly registered
    # runtime. Idempotent no-op when the registry holds nothing.
    service.teardown_runtime(session_id, service.runtime(session_id))
    # Re-read after the claim (cross-process discipline): the row we
    # spawn for is the one we just transitioned, never a stale snapshot.
    claimed = service.get_session(session_id, workspace_id)
    handle = spawn_session_runtime(
        service.db,
        service._settings,
        service._registry,
        service._runtimes,
        service._runtimes_lock,
        ServiceCallbacks(service, session_id),
        session_id,
        agent,
        user_id,
        workspace_id,
        resume_acp_session_id=claimed["acp_session_id"],
    )
    runtime = service.runtime(session_id)
    if runtime is not None and not handle.loaded_existing:
        with runtime.lock:
            runtime.resume_transcript_pending = True
    # A close that raced the spawn owns the final state: the misleading
    # session_resumed row must not land on an already-closed timeline.
    latest = service.db.get_studio_chat_session(session_id) or {}
    if str(latest.get("status")) in ("starting", "idle", "running", "awaiting_permission"):
        service.store.append_message(session_id, "status", "system", {"event": "session_resumed"})
    service.store.publish_session(session_id)
    return service.get_session(session_id)
