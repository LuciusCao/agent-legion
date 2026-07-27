from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.exceptions import HTTPException

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
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def require_workspace_access(
    request: Request,
    user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    """Workspace membership guard: viewers read, editors write, admins pass.

    Routes without a workspace_id path parameter only require a logged-in
    user. Non-members get 404 (not 403) so workspace existence cannot be
    enumerated.
    """
    if user.get("role") == "admin":
        return user
    workspace_id = request.path_params.get("workspace_id")
    if not workspace_id:
        return user
    role = request.app.state.job_db.get_workspace_role(str(workspace_id), str(user["id"]))
    if role is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    minimum = "viewer" if request.method in _SAFE_METHODS else "editor"
    if _MEMBER_ROLE_RANK.get(role, 0) < _MEMBER_ROLE_RANK[minimum]:
        raise HTTPException(status_code=403, detail="Insufficient workspace role")
    return user
