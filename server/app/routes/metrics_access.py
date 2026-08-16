"""Workspace membership guard for query-param-scoped metrics requests.

``/api/metrics/overview`` takes the workspace scope as a query parameter.
``require_workspace_access`` honours the ``workspace_id`` query parameter as
well, so the member case is already covered there; this guard adds the
remaining rule: global scope (no ``workspace_id``) is admin-only, members see
only workspaces they belong to. Non-members get 404 (not 403) so workspace
existence cannot be enumerated — same semantics as the path-parameter guard.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request


def enforce_workspace_membership(
    request: Request, workspace_id: str | None, user: dict[str, Any]
) -> None:
    if user.get("role") == "admin":
        return
    if workspace_id is None:
        raise HTTPException(status_code=403, detail="Admin role required for global metrics")
    role = request.app.state.job_db.get_workspace_role(workspace_id, str(user["id"]))
    if role is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
