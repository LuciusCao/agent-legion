"""Shared runtime-establishment path for studio chat sessions.

Split from service.py (file budget): create_session and resume_session both
mint the per-session run token, spawn the ACP subprocess handle, wait for
readiness, and funnel every startup failure through one cleanup path (guarded
error write, runtime teardown, token revoke). build_mcp_server_spec lives here
too — service.py was its only consumer (moved out of acp_session.py for budget).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from acp.schema import HttpHeader, HttpMcpServer

from server.app.auth.scoped_tokens import mint_scoped_token, revoke_scoped_token
from server.app.jobs import JobQueries
from server.app.mcp_server.config import SESSION_ID_HEADER
from server.app.mcp_server.http_app import MCP_URL_PATH
from server.app.services.job_errors import InvalidOperationError
from server.app.settings import Settings
from server.app.studio_chat.acp_session import AcpSessionCallbacks, AcpSessionHandle
from server.app.studio_chat.registry import StudioAgentRegistryStore
from server.app.studio_chat.runtime import SessionRuntime
from server.app.studio_chat.teardown import teardown_runtime

logger = logging.getLogger(__name__)

# Time to wait for the agent subprocess to finish initialize + session/new.
SESSION_START_TIMEOUT_SECONDS = 60


def build_mcp_server_spec(*, token: str, api_base: str, session_id: str) -> HttpMcpServer:
    """The session-scoped agent-legion MCP entry injected into session/new.

    kimi ≥ 0.38 only accepts http/sse MCP servers over ACP, so the backend
    serves the tool surface itself (server.app.mcp_server.http_app) and the
    session points at that URL. The raw scoped token crosses only as an HTTP
    header inside the ACP session/new request — never persisted, never logged
    (STUDIO-AGENT-001). The chat session id rides along (SESSION_ID_HEADER)
    so the get_studio_context tool can resolve this session's live context.
    """
    return HttpMcpServer(
        type="http",
        name="agent-legion-studio",
        url=f"{api_base}{MCP_URL_PATH}",
        headers=[
            HttpHeader(name="Authorization", value=f"Bearer {token}"),
            HttpHeader(name=SESSION_ID_HEADER, value=session_id),
        ],
    )


def spawn_session_runtime(
    db: JobQueries,
    settings: Settings,
    registry: StudioAgentRegistryStore,
    runtimes: dict[str, SessionRuntime],
    runtimes_lock: threading.Lock,
    callbacks: AcpSessionCallbacks,
    session_id: str,
    agent: dict[str, Any],
    user_id: str,
    workspace_id: str,
    *,
    resume_acp_session_id: str | None = None,
) -> AcpSessionHandle:
    """Mint the run token, spawn the agent subprocess, wait for readiness.

    The caller owns the session-row transition into 'starting' (creation INSERT
    or resume claim); this path raises after one shared cleanup so a failed
    start never leaves a 'starting' row, a leaked token, or an orphaned runtime.
    Returns the live handle (its ``loaded_existing`` flag tells the resume path
    whether session/load restored the prior context).
    """
    token: str | None = None
    runtime: SessionRuntime | None = None
    try:
        # The run token is bound to this session's workspace (schema v45):
        # the tool surface then refuses other workspaces for it.
        token = mint_scoped_token(db, user_id, origin="run", workspace_id=workspace_id)
        handle = AcpSessionHandle(
            command=str(agent["command"]),
            args=[str(arg) for arg in agent.get("args", [])],
            cwd=str(settings.root_dir),
            mcp_server=build_mcp_server_spec(
                token=token,
                api_base=str(registry.get()["api_base"]),
                session_id=session_id,
            ),
            env=None,
            callbacks=callbacks,
            resume_acp_session_id=resume_acp_session_id,
        )
        runtime = SessionRuntime(handle, token)
        # Pin the runtime identity on the callbacks BEFORE start: the ACP
        # thread's death-echo on_exit may only tear down this runtime, never
        # a newer one resume registered for the same session_id (ABA).
        with runtimes_lock:
            runtimes[session_id] = callbacks.runtime = runtime
        handle.start()
        if not handle.ready_event.wait(timeout=SESSION_START_TIMEOUT_SECONDS):
            raise InvalidOperationError("Studio agent failed to start (timeout)")
        session = db.get_studio_chat_session(session_id)
        if session is None or session["status"] != "idle":
            detail = (session or {}).get("error_detail") or "agent startup failed"
            raise InvalidOperationError(f"Studio agent failed to start: {detail}")
        return handle
    except Exception as exc:
        # One cleanup path for every startup failure: no half-applied
        # 'starting' row, no leaked token, no orphaned runtime. The status
        # write is guarded (#158): a close that raced the startup window
        # already owns the final 'closed' state.
        detail = str(exc) or exc.__class__.__name__
        db.update_studio_chat_session_if(
            session_id, status_not_in=("closed",), status="error", error_detail=detail[:500]
        )
        if runtime is None:
            # The handle never materialized: nothing to tear down, but a
            # minted token must not leak (token is None when the mint
            # itself failed).
            if token is not None:
                try:
                    revoke_scoped_token(db, token)
                except Exception:
                    logger.warning("failed to revoke studio chat token for %s", session_id)
        else:
            teardown_runtime(db, runtimes, runtimes_lock, session_id, runtime)
        raise
