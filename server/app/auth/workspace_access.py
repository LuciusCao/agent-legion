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

from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.exceptions import HTTPException

from server.app.auth.dependencies import _SAFE_METHODS, get_current_user
from server.app.auth.scope_binding import (
    refuse_foreign_binding,
    resolve_job_workspace_scope,
    scoped_binding_mismatch,
)
from server.app.auth.workspace_api_scope import (
    api_scope_route_scope as _workspace_scope,
)
from server.app.auth.workspace_api_scope import (
    refuse_off_allowlist_api_scope,
)

_MEMBER_ROLE_RANK = {"viewer": 1, "editor": 2}


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
    # the bound workspace — it is the runner of the intake channel only
    # (bound-workspace equality + the intake allowlist; 404 off-surface —
    # see refuse_off_allowlist_api_scope for the two narrow rules and the
    # dual-check on POST /runs).

    #971: the scoped-token binding runs BEFORE the admin fast path, in the
    same order as the job guard (auth/scope_binding.py owns the shared
    predicate and refusal): a workspace-bound token minted by an admin, or
    by a member of several workspaces, stays bound on every secured route.
    """
    if refuse_off_allowlist_api_scope(request, user):
        return user
    workspace_id = _workspace_scope(request)
    refuse_foreign_binding(user, workspace_id)
    if user.get("role") == "admin":
        return user
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
    ``auth.scope_binding.resolve_job_workspace_scope``), closing the bare-route IDOR.

    A scoped identity on a non-safe (effecting) method short-circuits past
    the job lookup: every effecting job route mounts
    ``reject_studio_agent_scope`` and answers 403 regardless of whether the
    job exists — the scope refusal stays ahead of any job existence signal
    (test_studio_agent_job_tools pins this ordering).

    #626: the api-scope machine identity carries no member row and no
    user['id'], so it must never reach the membership lookup — same shared
    arm as ``require_workspace_access`` (refuse_off_allowlist_api_scope);
    POST /runs gets past the scoped effecting short-circuit above and is
    admitted (or not) by the route-level ``require_workspace_api_intake``.
    """
    if user.get("actor_scope") and request.method not in _SAFE_METHODS:
        return user
    if refuse_off_allowlist_api_scope(request, user):
        return user
    # Malformed job ids (NUL bytes etc.) are rejected before the job lookup:
    # the scope resolution queries by the raw path param, and psycopg would
    # turn an embedded NUL into a DataError (500) instead of a clean miss.
    job_id = request.path_params.get("job_id")
    if job_id is not None and not job_id.isprintable():
        raise HTTPException(status_code=400, detail="Invalid job id")
    # Scoped-token binding runs before the admin fast path (see
    # auth/scope_binding.py, shared with require_workspace_access — #971):
    # a workspace-bound run token must stay bound even when the minter is
    # an admin.
    workspace_id = resolve_job_workspace_scope(request, user)
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


def require_scoped_workspace_match(
    workspace_id: str,
    user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    """Scoped-token binding guard with no-enumeration semantics (#631).

    Companion to require_workspace_access for mixed-audience read surfaces
    that answer cross-workspace probes with 404: the membership guard checks
    the minting user's role/membership and cannot see the Bearer token's
    ``scoped_workspace_id``, so without this check a workspace-bound scoped
    token could read through any workspace its user can see. A 403 would leak
    which workspaces exist for that user — mismatches stay 404. Full sessions
    and unbound scoped tokens pass (membership-only, schema v45 / #158); for
    403-style enforcement see ``enforce_scoped_workspace_binding``
    (auth/dependencies.py, studio chat).
    """
    if scoped_binding_mismatch(user, workspace_id):
        raise HTTPException(status_code=404, detail="Workspace not found")
    return user
