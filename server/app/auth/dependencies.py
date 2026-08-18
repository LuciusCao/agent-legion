from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.exceptions import HTTPException

from server.app.auth.scoped_tokens import STUDIO_AGENT_SCOPE

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
    """
    token, channel = extract_session_token(request)
    if token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user: dict[str, Any] | None = request.app.state.auth_service.authenticate(token)
    if user is None and channel == "bearer":
        # Scoped tokens (studio agent runs) authenticate via Bearer only.
        user = request.app.state.auth_service.authenticate_scoped(token)
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
    user: Annotated[dict[str, Any], Depends(require_studio_agent_scope)],
) -> dict[str, Any]:
    """Refuse a workspace-bound run token operating on another workspace."""
    # Schema v45 (STUDIO-AGENT-001): unbound self-service tokens keep the
    # previous membership-only behaviour.
    bound = user.get("scoped_workspace_id")
    if bound and bound != workspace_id:
        raise HTTPException(status_code=403, detail="Scoped token bound to another workspace")
    return user
