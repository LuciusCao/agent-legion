"""Auth dependency injection for user-identity routes (sessions, scoped tokens).

Bearer wins over the session cookie; scoped tokens are Bearer-only, never
ambient (CSRF-exempt — STUDIO-AGENT-001). Guards on ``get_current_user``:
``require_user`` (a real user identity — user sessions and studio-agent
scoped tokens, which carry the initiating user's row; the workspace API
machine identity is refused, #626 HIGH-1), ``require_admin`` (role + refusal
of ANY scoped identity — it inherits the minter's role),
``reject_studio_agent_
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

from server.app.auth.api_token_identity import resolve_api_token_identity
from server.app.auth.scope_binding import refuse_foreign_binding
from server.app.auth.scoped_tokens import STUDIO_AGENT_SCOPE
from server.app.auth.workspace_api_tokens import WORKSPACE_API_SCOPE

SESSION_COOKIE = "agent_legion_session"
CSRF_HEADER = "x-agent-legion-request"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_MEMBER_ROLE_RANK = {"viewer": 1, "editor": 2}


def extract_session_token(request: Request) -> tuple[str | None, str | None]:
    """Return (token, channel); Bearer header wins over the session cookie."""
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token:
            return token, "bearer"
    cookie_token = request.cookies.get(SESSION_COOKIE)
    return (cookie_token, "cookie") if cookie_token else (None, None)


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
    auth = request.app.state.auth_service
    user: dict[str, Any] | None = auth.authenticate(token)
    if user is None and channel == "bearer":
        # Scoped tokens and workspace API tokens are Bearer-only. The latter
        # also charges its per-token request bucket during resolution (#738).
        user = auth.authenticate_scoped(token) or resolve_api_token_identity(request, token)
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


CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]


def require_user(user: CurrentUser) -> dict[str, Any]:
    """Require a real user identity, not the workspace API machine identity."""
    if user.get("actor_scope") == WORKSPACE_API_SCOPE:
        raise HTTPException(
            status_code=403, detail="Workspace API tokens cannot use user endpoints"
        )
    return user


def _refuse_scoped(user: dict[str, Any], action: str) -> None:
    scope = user.get("actor_scope")
    if scope:
        subject = "Studio agent scope" if scope == STUDIO_AGENT_SCOPE else "Scoped tokens"
        raise HTTPException(status_code=403, detail=f"{subject} cannot {action}")


def require_admin(user: CurrentUser) -> dict[str, Any]:
    """Require an unscoped admin identity; scoped tokens inherit the minter's role."""
    _refuse_scoped(user, "use admin endpoints")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def reject_studio_agent_scope(user: CurrentUser) -> dict[str, Any]:
    """Effecting-endpoint guard: every scoped identity gets 403."""
    _refuse_scoped(user, "take effect")
    return user


def require_studio_agent_scope(user: CurrentUser) -> dict[str, Any]:
    """Only studio-agent scoped tokens may call the studio tool surface."""
    if user.get("actor_scope") != STUDIO_AGENT_SCOPE:
        raise HTTPException(status_code=403, detail="Studio agent scoped token required")
    return user


def require_workspace_role(
    request: Request,
    user: dict[str, Any],
    workspace_id: str | None,
    not_found: str = "Workspace not found",
    membership_only: bool = False,
) -> dict[str, Any]:
    """Apply the shared admin/membership/role arm after route-specific scope checks."""
    if user.get("role") == "admin" or not workspace_id:
        return user
    role = request.app.state.job_db.get_workspace_role(str(workspace_id), str(user["id"]))
    if role is None:
        raise HTTPException(status_code=404, detail=not_found)
    minimum = "viewer" if request.method in _SAFE_METHODS else "editor"
    if not membership_only and _MEMBER_ROLE_RANK.get(role, 0) < _MEMBER_ROLE_RANK[minimum]:
        raise HTTPException(status_code=403, detail="Insufficient workspace role")
    return user


def require_studio_agent_workspace(
    workspace_id: str,
    request: Request,
    user: Annotated[dict[str, Any], Depends(require_studio_agent_scope)],
) -> dict[str, Any]:
    """Bind run tokens to their workspace; unbound tokens require membership."""
    refuse_foreign_binding(user, workspace_id)
    if not user.get("scoped_workspace_id"):
        require_workspace_role(request, user, workspace_id, membership_only=True)
    return user


def enforce_scoped_workspace_binding(workspace_id: str, user: CurrentUser) -> dict[str, Any]:
    """Read-side guard: a bound scoped token may only read its own workspace."""
    refuse_foreign_binding(user, workspace_id)
    return user
