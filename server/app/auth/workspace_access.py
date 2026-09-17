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


def _job_workspace_id(request: Request, workspace_id: str | None) -> str | None:
    """Resolve the workspace a job-id-shaped route actually addresses (#710).

    ``job_id`` embeds its workspace (``{workspace_id}_{workflow_key}_{source_id}``)
    but the separator is legal inside workspace ids too, so the scope cannot
    be parsed from the id — it is read from the job row itself:

    - bare ``/jobs/{job_id}`` routes: the job's workspace is the scope;
    - ``/workspaces/{workspace_id}/jobs/{job_id}`` routes: the path scope must
      match the job's actual workspace, so one's own workspace prefix cannot
      borrow another workspace's job id (defense in depth ahead of the
      service-level ``_require_job`` checks).

    Unknown jobs 404 like unknown workspaces — enumeration-safe.
    """
    job_id = request.path_params.get("job_id")
    if job_id is None:
        return workspace_id
    job = request.app.state.job_db.get_job(str(job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    job_workspace = str(job["workspace_id"])
    if workspace_id is not None and str(workspace_id) != job_workspace:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_workspace


def require_workspace_access(
    request: Request,
    user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    """Workspace membership guard: viewers read, editors write, admins pass.

    The workspace scope is read from the ``workspace_id`` path parameter,
    falling back to the ``workspace_id`` query parameter for routes that take
    the scope in the query string (``/api/worker/*``, ``/api/metrics/overview``).
    On ``job_id``-shaped routes the scope comes from the job's own workspace
    (see ``_job_workspace_id``). Routes without any workspace scope only
    require a logged-in user. Non-members get 404 (not 403) so workspace
    existence cannot be enumerated.
    """
    if user.get("role") == "admin":
        return user
    workspace_id = request.path_params.get("workspace_id") or request.query_params.get(
        "workspace_id"
    )
    workspace_id = _job_workspace_id(request, workspace_id)  # type: ignore[arg-type]
    if not workspace_id:
        return user
    role = request.app.state.job_db.get_workspace_role(str(workspace_id), str(user["id"]))
    if role is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    minimum = "viewer" if request.method in _SAFE_METHODS else "editor"
    if _MEMBER_ROLE_RANK.get(role, 0) < _MEMBER_ROLE_RANK[minimum]:
        raise HTTPException(status_code=403, detail="Insufficient workspace role")
    return user
