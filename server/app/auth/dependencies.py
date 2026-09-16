"""Auth dependency injection for user-identity routes (sessions, scoped tokens).

Bearer wins over the session cookie; scoped tokens are Bearer-only, never
ambient (CSRF-exempt — STUDIO-AGENT-001). Guards on ``get_current_user``:
``require_user`` (any identity), ``require_admin`` (role + refusal of ANY
scoped identity — it inherits the minter's role), ``reject_studio_agent_
scope`` (effecting endpoints), ``require_studio_agent_scope``/``_workspace``
(tool surface), ``enforce_scoped_workspace_binding`` (bound tokens read only
their own workspace). The workspace API intake token (#626) resolves in the
same chain into a machine identity (actor_scope='api'); the runs router
mounts ``require_workspace_api_intake`` (auth/workspace_access.py) so only
that surface admits it. Worker-token auth (routes/agent_workers.py) and the
studio MCP mount (ASGI-level check) are not routed through here.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.exceptions import HTTPException

from server.app.auth.scoped_tokens import STUDIO_AGENT_SCOPE
from server.app.auth.workspace_api_tokens import (
    WORKSPACE_API_SCOPE,
    split_api_token,
)

SESSION_COOKIE = "agent_legion_session"
CSRF_HEADER = "x-agent-legion-request"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def extract_session_token(request: Request) -> tuple[str | None, str | None]:
    """Return (token, channel); Bearer header wins over the session cookie."""
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token:
            return token, "bearer"
    cookie_token = request.cookies.get(SESSION_COOKIE)
    if cookie_token:
        return cookie_token, "cookie"
    return None, None


def get_current_user(request: Request) -> dict[str, Any]:
    """Resolve the session to a user; 401 when anonymous or expired.

    Cookie-authenticated mutations must carry the CSRF header (a cross-site
    form/fetch cannot set custom headers), which pins cookie auth to same-site
    frontend calls. Bearer-channel callers are exempt: they are not ambient.
    Resolution order on the Bearer channel: user session → scoped token →
    workspace API token (#626, ``{token_id}.{secret}`` shape only) → machine
    identity dict with actor_scope='api' and NO user id — downstream guards
    (workspace_access) treat it as an editor bound to its one workspace.
    """
    token, channel = extract_session_token(request)
    if token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user: dict[str, Any] | None = request.app.state.auth_service.authenticate(token)
    if user is None and channel == "bearer":
        # Scoped tokens (studio agent runs) authenticate via Bearer only.
        user = request.app.state.auth_service.authenticate_scoped(token)
    if user is None and channel == "bearer" and split_api_token(token) is not None:
        # Workspace API intake tokens (#626): same Bearer-only, CSRF-exempt
        # rule as scoped tokens. Only the {token_id}.{secret} shape reaches
        # the store — a session/scoped token can never collide with it.
        resolved = request.app.state.workspace_api_token_store.resolve_api_token(token)
        if resolved is not None:
            user = {
                "actor_scope": WORKSPACE_API_SCOPE,
                "scoped_workspace_id": resolved["workspace_id"],
                "api_token_id": resolved["token_id"],
            }
    if user is None:
        raise HTTPException(status_code=401, detail="Session expired or revoked")
    if (
        channel == "cookie"
        and request.method not in _SAFE_METHODS
        and request.headers.get(CSRF_HEADER) != "1"
    ):
        raise HTTPException(status_code=403, detail="Missing request header")
    request.state.current_user = user
    return user


def require_user(user: Annotated[dict[str, Any], Depends(get_current_user)]) -> dict[str, Any]:
    return user


def require_admin(user: Annotated[dict[str, Any], Depends(get_current_user)]) -> dict[str, Any]:
    # A scoped token inherits the initiating user's role; without this check a
    # token minted for an admin would pass require_admin and reach every admin
    # endpoint (STUDIO-AGENT-001: scoped identities never take effect).
    scope = user.get("actor_scope")
    if scope:
        detail = (
            "Studio agent scope cannot use admin endpoints"
            if scope == STUDIO_AGENT_SCOPE
            else "Scoped tokens cannot use admin endpoints"
        )
        raise HTTPException(status_code=403, detail=detail)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def reject_studio_agent_scope(
    user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    """Effecting-endpoint guard: scoped tokens get 403 (STUDIO-AGENT-001).

    Aligned with require_admin: any non-empty actor_scope is refused, not just
    the studio-agent scope, so a future scope type cannot silently inherit
    effecting rights.
    """
    scope = user.get("actor_scope")
    if scope:
        detail = (
            "Studio agent scope cannot take effect"
            if scope == STUDIO_AGENT_SCOPE
            else "Scoped tokens cannot take effect"
        )
        raise HTTPException(status_code=403, detail=detail)
    return user


def require_studio_agent_scope(
    user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    """Tool-surface guard: only studio-agent scoped tokens may call the
    ``/api/studio-agent/tools/*`` endpoints; full user sessions get 403
    (STUDIO-AGENT-001)."""
    if user.get("actor_scope") != STUDIO_AGENT_SCOPE:
        raise HTTPException(status_code=403, detail="Studio agent scoped token required")
    return user


def require_studio_agent_workspace(
    workspace_id: str,
    request: Request,
    user: Annotated[dict[str, Any], Depends(require_studio_agent_scope)],
) -> dict[str, Any]:
    """Refuse a workspace-bound run token operating on another workspace.

    #710 follow-up (product decision: skills and the whole studio-agent tool
    surface are workspace-isolated): an UNBOUND self-service token (origin
    'user', no workspace binding) now falls back to a membership check — the
    minter must be a member of the addressed workspace (viewer suffices for
    reads; the write routes gate mutating verbs themselves via the tool
    contracts). Previously bound=None passed through with no workspace
    relation at all, which on the skill tools meant a leaked unbound token
    held read AND commit+tag rights over every workspace's skill repos."""
    bound = user.get("scoped_workspace_id")
    if bound and bound != workspace_id:
        raise HTTPException(status_code=403, detail="Scoped token bound to another workspace")
    if not bound and user.get("role") != "admin":
        role = request.app.state.job_db.get_workspace_role(str(workspace_id), str(user["id"]))
        if role is None:
            # 404, matching the workspace guard's enumeration-safe refusal.
            raise HTTPException(status_code=404, detail="Workspace not found")
    return user


def enforce_scoped_workspace_binding(
    workspace_id: str,
    user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    """Read-side guard for mixed audiences (#158): full sessions pass through,
    a workspace-bound scoped token may only read its own workspace.

    The effecting surface refuses scoped tokens outright
    (``reject_studio_agent_scope``); chat reads stay reachable for the agent's
    own session, but without this check a leaked run token could read every
    workspace the initiating user can see (messages and the SSE stream).
    """
    bound = user.get("scoped_workspace_id")
    if bound and bound != workspace_id:
        raise HTTPException(status_code=403, detail="Scoped token bound to another workspace")
    return user
