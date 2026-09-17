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
- and the module stays under its file budget in its own home. #626 adds the
  machine-identity arm: a workspace API intake token (actor_scope='api') is
  the editor of exactly its bound workspace — never a member row, never an
  admin, never another workspace.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.exceptions import HTTPException

from server.app.auth.dependencies import _SAFE_METHODS, get_current_user
from server.app.auth.workspace_api_tokens import WORKSPACE_API_SCOPE

_MEMBER_ROLE_RANK = {"viewer": 1, "editor": 2}

# #626 review: the surface allowlist for api-scope machine identities. The
# intake channel's documented surface (#626) is submit + run/job status
# reads: the runs router's POST/GETs and the workspace jobs listings.
# Everything else under this guard — secrets/materials/chat-session/
# preview-panel/metrics/etc. reads, and every scopeless or off-allowlist
# mount — must refuse the machine identity (404, the no-enumeration
# refusal) instead of inheriting the "editor of the bound workspace" pass.
# (method, route template); templates compile to exact full-path patterns
# ({param} → one path segment, no trailing anything) — a prefix or
# substring match here would widen the surface again. POST /runs appears
# here because the job_route_group mounts this guard on the whole runs
# router; the route-level require_workspace_api_intake does the admission.
# GET /jobs is the legacy 500-cap listing; codex3 P1 adds the paginated
# /jobs/snapshot (cursor + run_id filter) so a machine caller can actually
# reach the WHOLE job status surface — a run with more items than the
# legacy cap, or a workspace with newer jobs, is otherwise unreadable.
_API_SCOPE_ALLOWLIST: tuple[tuple[str, str], ...] = (
    ("POST", "/api/workspaces/{workspace_id}/runs"),
    ("GET", "/api/workspaces/{workspace_id}/runs"),
    ("GET", "/api/workspaces/{workspace_id}/runs/{run_id}"),
    ("GET", "/api/workspaces/{workspace_id}/jobs"),
    ("GET", "/api/workspaces/{workspace_id}/jobs/snapshot"),
)


def _api_scope_route_allowed(method: str, path: str) -> bool:
    """Exact match of (method, concrete path) against the allowlist."""
    return any(
        method == m and re.fullmatch(re.sub(r"\{[^/]+\}", r"[^/]+", template), path)
        for m, template in _API_SCOPE_ALLOWLIST
    )


def _workspace_scope(request: Request) -> str | None:
    """The workspace scope of the current route (path param, then query)."""
    return request.path_params.get("workspace_id") or request.query_params.get("workspace_id")


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

    #626 review: the api-scope machine identity is NOT a general member of
    the bound workspace — it is the runner of the intake channel only. Two
    rules, both narrow (an api token's entire permission model is ONE
    workspace and the documented intake surface):
    1. hard equality with the route's workspace scope (a mismatched or
       missing scope gets the same 404 as a non-member, no enumeration);
    2. (method, path) must be on the intake allowlist (_API_SCOPE_ALLOWLIST:
       POST/GET runs + the jobs listings, legacy AND paginated) — every
       other route under this guard, including OTHER GETs (secrets,
       materials, chat sessions, preview panels, metrics) and scopeless
       mounts, 404s the machine identity.
       The POST /runs admission is dual-checked: the job_route_group mounts
       this guard router-wide, and the route-level
       require_workspace_api_intake (auth/api_intake.py) re-verifies the
       binding — it is the ONLY effecting surface; the pre-fix "editor
       pass" arm let the api token into every workspace-scoped GET plus the
       scopeless-guard fall-through and crashed 500 on user['id'] handlers
       (node-code and agent-definition draft writes, metrics overview).
    """
    if user.get("role") == "admin":
        return user
    if user.get("actor_scope") == WORKSPACE_API_SCOPE:
        scope = _workspace_scope(request)
        bound = user.get("scoped_workspace_id")
        if (
            not scope
            or bound != scope
            or not _api_scope_route_allowed(request.method, request.url.path)
        ):
            raise HTTPException(status_code=404, detail="Workspace not found")
        return user
    workspace_id = _workspace_scope(request)
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
