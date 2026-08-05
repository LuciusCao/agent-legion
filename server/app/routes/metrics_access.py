"""Workspace membership guard for query-param-scoped metrics requests.

``require_workspace_access`` only inspects the ``workspace_id`` *path*
parameter; ``/api/metrics/overview`` takes the workspace scope as a query
parameter, so the membership check lives here. Non-members get 404 (not
403) so workspace existence cannot be enumerated — same semantics as the
path-parameter guard.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request


def enforce_workspace_membership(
    request: Request, workspace_id: str | None, user: dict[str, Any]
) -> None:
    if workspace_id is None or user.get("role") == "admin":
        return
    role = request.app.state.job_db.get_workspace_role(workspace_id, str(user["id"]))
    if role is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
