"""Workspace membership guard for workspace-scoped routes.

Split from dependencies.py: the guard grew a query-parameter fallback
(routes like ``/api/worker/*`` take the workspace scope in the query string),
and the module stays under its file budget in its own home.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.exceptions import HTTPException

from server.app.auth.dependencies import _SAFE_METHODS, get_current_user

_MEMBER_ROLE_RANK = {"viewer": 1, "editor": 2}


def require_workspace_access(
    request: Request,
    user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    """Workspace membership guard: viewers read, editors write, admins pass.

    The workspace scope is read from the ``workspace_id`` path parameter,
    falling back to the ``workspace_id`` query parameter for routes that take
    the scope in the query string (``/api/worker/*``, ``/api/metrics/overview``).
    Routes without either only require a logged-in user. Non-members get 404
    (not 403) so workspace existence cannot be enumerated.
    """
    if user.get("role") == "admin":
        return user
    workspace_id = request.path_params.get("workspace_id") or request.query_params.get(
        "workspace_id"
    )
    if not workspace_id:
        return user
    role = request.app.state.job_db.get_workspace_role(str(workspace_id), str(user["id"]))
    if role is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    minimum = "viewer" if request.method in _SAFE_METHODS else "editor"
    if _MEMBER_ROLE_RANK.get(role, 0) < _MEMBER_ROLE_RANK[minimum]:
        raise HTTPException(status_code=403, detail="Insufficient workspace role")
    return user
