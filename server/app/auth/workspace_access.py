"""Workspace membership guards for workspace-scoped routes.

Split from dependencies.py: the guard grew a query-parameter fallback
(routes like ``/api/worker/*`` take the workspace scope in the query string),
and the module stays under its file budget in its own home.

Two guards live here (#710):

- ``require_workspace_access`` — the original membership guard for the
  generic ``secured()`` surface: workspace_id path/query scope only. Its
  scoped-token binding refusal (#971) is the same 403 "bound" shape the
  studio-agent tools (``require_studio_agent_workspace``) and chat reads
  (``enforce_scoped_workspace_binding``) already answered, so those
  surfaces' contracts are unchanged.
- ``require_job_workspace_access`` — the same membership logic, preceded by
  a job-ownership resolution for the job-id routes (``job_group`` only):
  bare ``/jobs/{job_id}`` endpoints had no workspace scope at all, so any
  logged-in user could read, mutate, or delete another workspace's jobs.

Both guards take the binding decision from ``auth.scope_binding`` in one
order (api-scope arm → binding → admin → membership, #971).

#626 adds the machine-identity arm both guards share: a workspace API
intake token (actor_scope='api') is the editor of exactly its bound
workspace — never a member row, never an admin, never another workspace;
that arm lives in ``workspace_api_scope`` (file budget).
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import HTTPException

from server.app.auth import dependencies as auth
from server.app.auth import scope_binding
from server.app.auth import workspace_api_scope as api_scope


def require_workspace_access(request: Request, user: auth.CurrentUser) -> dict[str, Any]:
    """Workspace membership guard: viewers read, editors write, admins pass.

    API-scope admission and scoped-token binding precede the admin/membership
    arm, so neither machine identities nor admin-minted run tokens can escape
    their narrow workspace surface. Non-members get an enumeration-safe 404.
    """
    if api_scope.refuse_off_allowlist_api_scope(request, user):
        return user
    workspace_id = api_scope.api_scope_route_scope(request)
    scope_binding.refuse_foreign_binding(user, workspace_id)
    return auth.require_workspace_role(request, user, workspace_id)


def require_job_workspace_access(
    request: Request,
    user: auth.CurrentUser,
) -> dict[str, Any]:
    """Authorize job routes by the addressed job's own workspace.

    Effecting scoped identities short-circuit before the job lookup because
    every such route mounts ``reject_studio_agent_scope``. Safe reads resolve
    the job row first; unknown, foreign, path-mismatched and off-binding jobs
    share the same 404 shape.
    """
    if user.get("actor_scope") and request.method not in auth._SAFE_METHODS:
        return user
    if api_scope.refuse_off_allowlist_api_scope(request, user):
        return user
    # Reject malformed ids before psycopg can turn them into a 500 DataError.
    job_id = request.path_params.get("job_id")
    if job_id is not None and not job_id.isprintable():
        raise HTTPException(status_code=400, detail="Invalid job id")
    workspace_id = scope_binding.resolve_job_workspace_scope(request, user)
    not_found = "Job not found" if job_id is not None else "Workspace not found"
    return auth.require_workspace_role(request, user, workspace_id, not_found=not_found)


def require_scoped_workspace_match(
    workspace_id: str,
    user: auth.CurrentUser,
) -> dict[str, Any]:
    """Mixed-audience reads hide off-binding workspaces behind a 404 (#631)."""
    if scope_binding.scoped_binding_mismatch(user, workspace_id):
        raise HTTPException(status_code=404, detail="Workspace not found")
    return user
