"""Workspace membership guards for workspace-scoped routes.

Split from dependencies.py: the guard grew a query-parameter fallback
(routes like ``/api/worker/*`` take the workspace scope in the query string),
and the module stays under its file budget in its own home.

Two guards live here (#710):

- ``require_workspace_access`` — the original membership guard for the
  generic ``secured()`` surface: workspace_id path/query scope only. Kept
  byte-for-byte in semantics so surfaces with their own scoped-token
  contracts (studio-agent tools: ``require_studio_agent_workspace`` and its
  403 "bound" refusal; chat reads: ``enforce_scoped_workspace_binding``)
  keep their ordering.
- ``require_job_workspace_access`` — the same membership logic, preceded by
  a job-ownership resolution for the job-id routes (``job_group`` only):
  bare ``/jobs/{job_id}`` endpoints had no workspace scope at all, so any
  logged-in user could read, mutate, or delete another workspace's jobs.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.exceptions import HTTPException

from server.app.auth.dependencies import _SAFE_METHODS, get_current_user

_MEMBER_ROLE_RANK = {"viewer": 1, "editor": 2}


def _resolve_job_workspace_scope(request: Request, user: dict[str, Any]) -> str | None:
    """Resolve the workspace a job-id route actually addresses (#710).

    ``job_id`` embeds its workspace (``{workspace_id}_{workflow_key}_{source_id}``)
    but the separator is legal inside workspace ids too, so the scope cannot
    be parsed from the id — it is read from the job row itself (id-only
    projection; jobs rows carry KB-scale TEXT columns and this runs per
    request):

    - bare ``/jobs/{job_id}`` routes: the job's workspace is the scope;
    - ``/workspaces/{workspace_id}/jobs/{job_id}`` routes: the path scope must
      match the job's actual workspace, so one's own workspace prefix cannot
      borrow another workspace's job id (defense in depth ahead of the
      service-level per-item checks).

    A workspace-bound scoped token additionally refuses every workspace other
    than its binding — same shape as ``enforce_scoped_workspace_binding``
    (#158), which these bare routes previously bypassed. This holds for
    admin minters too: a scoped token inherits the minter's role
    (``require_admin`` refuses scoped identities for the same reason), so the
    admin fast path runs only after this binding check.

    Unknown jobs 404 like unknown workspaces — enumeration-safe, and the
    detail text is uniform so the two cases are indistinguishable.
    """
    job_id = request.path_params.get("job_id")
    workspace_id = request.path_params.get("workspace_id") or request.query_params.get(
        "workspace_id"
    )
    workspace_id = str(workspace_id) if workspace_id else None
    if job_id is not None:
        job_workspace = request.app.state.job_db.get_job_workspace(str(job_id))
        if job_workspace is None or (workspace_id is not None and workspace_id != job_workspace):
            raise HTTPException(status_code=404, detail="Job not found")
        workspace_id = job_workspace
        bound = user.get("scoped_workspace_id")
        if bound and workspace_id is not None and str(bound) != workspace_id:
            raise HTTPException(status_code=404, detail="Job not found")
    return workspace_id


def require_workspace_access(
    request: Request,
    user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    """Workspace membership guard: viewers read, editors write, admins pass.

    The workspace scope is read from the ``workspace_id`` path parameter,
    falling back to the ``workspace_id`` query parameter for routes that take
    the scope in the query string (``/api/worker/*``, ``/api/metrics/overview``).
    Routes without a workspace scope only require a logged-in user.
    Non-members get 404 (not 403) so workspace existence cannot be enumerated.
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


def require_job_workspace_access(
    request: Request,
    user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    """``require_workspace_access`` for the job routes: the authorization
    scope additionally comes from the addressed job's own workspace (see
    ``_resolve_job_workspace_scope``), closing the bare-route IDOR.

    A scoped identity on a non-safe (effecting) method short-circuits past
    the job lookup: every effecting job route mounts
    ``reject_studio_agent_scope`` and answers 403 regardless of whether the
    job exists — the scope refusal stays ahead of any job existence signal
    (test_studio_agent_job_tools pins this ordering).
    """
    if user.get("actor_scope") and request.method not in _SAFE_METHODS:
        return user
    # Scoped-token binding runs before the admin fast path (see
    # _resolve_job_workspace_scope): a workspace-bound run token must stay
    # bound even when the minter is an admin.
    workspace_id = _resolve_job_workspace_scope(request, user)
    if user.get("role") == "admin":
        return user
    if not workspace_id:
        return user
    role = request.app.state.job_db.get_workspace_role(str(workspace_id), str(user["id"]))
    if role is None:
        # On job-id routes a membership 404 must read exactly like the
        # unknown-job 404 above, or the differing detail strings become an
        # existence oracle (review R1 P3).
        detail = (
            "Job not found"
            if request.path_params.get("job_id") is not None
            else "Workspace not found"
        )
        raise HTTPException(status_code=404, detail=detail)
    minimum = "viewer" if request.method in _SAFE_METHODS else "editor"
    if _MEMBER_ROLE_RANK.get(role, 0) < _MEMBER_ROLE_RANK[minimum]:
        raise HTTPException(status_code=403, detail="Insufficient workspace role")
    return user
