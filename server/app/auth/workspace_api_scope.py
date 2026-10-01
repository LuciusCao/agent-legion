"""Api-scope machine-identity arm for the workspace membership guards（#626，
rebase 到 #745 后拆自 ``workspace_access``：文件预算——job_id 守卫加高后
两份同型分支再留在原文件必超带）。一个 workspace API intake token
（actor_scope='api'）的全部权限模型是「恰好一个绑定 workspace + 文档化的
intake 面」：本模块只裁决这条窄边界，成员行/角色判定仍归
``workspace_access``。"""

from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import HTTPException

from server.app.auth.workspace_api_tokens import WORKSPACE_API_SCOPE

# #626 review: the surface allowlist for api-scope machine identities. The
# intake channel's documented surface (#626) is submit + run/job status
# reads: the runs router's POST/GETs and the workspace jobs listings.
# Everything else under the guards — secrets/materials/chat-session/
# preview-panel/metrics/etc. reads, and every scopeless or off-allowlist
# mount — must refuse the machine identity (404, the no-enumeration
# refusal) instead of inheriting a member-style pass.
# (method, route template) pairs, matched against the RESOLVED route's
# template (request.scope["route"].path), never the concrete URL: path-text
# matching cannot tell /jobs/facets (a real static sibling) apart from
# /jobs/{job_id} with job_id="facets", and a prefix/substring/regex match
# here would widen the surface — the train review's regression pin covers
# exactly that collision. POST /runs appears here because the
# job_route_group mounts a membership guard on the whole runs router; the
# route-level require_workspace_api_intake does the admission. GET /jobs is
# the legacy 500-cap listing; codex3 P1 adds the paginated /jobs/snapshot
# (cursor + run_id filter) so a machine caller can actually reach the WHOLE
# job status surface — a run with more items than the legacy cap, or a
# workspace with newer jobs, is otherwise unreadable. The final three GETs
# are the #631 external read surface the intake loop polls after submit
# (status → manifest → raw bytes, routes/external_artifacts.py); they are
# GET-only, the workspace binding check above still runs first, and the
# service keeps the per-job ownership 404 — the train review (#779 P1-1)
# found them missing here, which 404'd the machine identity before the
# router.
_API_SCOPE_ALLOWLIST: tuple[tuple[str, str], ...] = (
    ("POST", "/api/workspaces/{workspace_id}/runs"),
    ("GET", "/api/workspaces/{workspace_id}/runs"),
    ("GET", "/api/workspaces/{workspace_id}/runs/{run_id}"),
    ("GET", "/api/workspaces/{workspace_id}/jobs"),
    ("GET", "/api/workspaces/{workspace_id}/jobs/snapshot"),
    ("GET", "/api/workspaces/{workspace_id}/jobs/{job_id}"),
    ("GET", "/api/workspaces/{workspace_id}/jobs/{job_id}/artifacts"),
    ("GET", "/api/workspaces/{workspace_id}/jobs/{job_id}/artifacts/{artifact_name:path}/raw"),
)


def _api_scope_route_allowed(request: Request) -> bool:
    """Exact (method, matched route template) membership in the allowlist.

    ``request.scope["route"]`` is set by the matched APIRoute before any
    dependency runs (fastapi.routing puts it into the child scope); a
    missing route means the request was never routed, which fails closed.
    """
    route = request.scope.get("route")
    return (request.method, getattr(route, "path", None)) in _API_SCOPE_ALLOWLIST


def api_scope_route_scope(request: Request) -> str | None:
    """The workspace scope of the current route (path param, then query)."""
    return request.path_params.get("workspace_id") or request.query_params.get("workspace_id")


def refuse_off_allowlist_api_scope(request: Request, user: dict) -> bool:
    """Refuse an api-scope identity that is off its narrow surface: True
    after raising the enumeration-safe 404 is NOT this function's shape —
    it raises directly (both call sites are guards whose refusal must be
    the same 404 a non-member gets); returns True only when the identity
    is allowed, so callers ``return user`` on True.

    Two narrow rules, both required (an api token's entire permission
    model is ONE workspace and the documented intake surface):
    1. hard equality with the route's workspace scope (a mismatched or
       missing scope gets the same 404 as a non-member, no enumeration);
    2. (method, matched route template) must be on the intake allowlist —
       every other route, including OTHER GETs (secrets, materials, chat
       sessions, preview panels, metrics) and scopeless mounts, 404s the
       machine identity.
    """
    if user.get("actor_scope") != WORKSPACE_API_SCOPE:
        return False
    scope = api_scope_route_scope(request)
    bound = user.get("scoped_workspace_id")
    if not scope or bound != scope or not _api_scope_route_allowed(request):
        raise HTTPException(status_code=404, detail="Workspace not found")
    return True
