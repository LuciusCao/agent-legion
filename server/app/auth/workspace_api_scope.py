"""Api-scope machine-identity arm for the workspace membership guards（#626，
rebase 到 #745 后拆自 ``workspace_access``：文件预算——job_id 守卫加高后
两份同型分支再留在原文件必超带）。一个 workspace API intake token
（actor_scope='api'）的全部权限模型是「恰好一个绑定 workspace + 文档化的
intake 面」：本模块只裁决这条窄边界，成员行/角色判定仍归
``workspace_access``。"""

from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import HTTPException

from server.app.auth.api_scope_surface import api_scope_route_allowed
from server.app.auth.workspace_api_tokens import WORKSPACE_API_SCOPE

# #626 review: the surface allowlist for api-scope machine identities. The
# intake channel's documented surface (#626) is submit + run/job status
# reads: the runs router's POST/GETs and the workspace jobs listings.
# Everything else under the guards — secrets/materials/chat-session/
# preview-panel/metrics/etc. reads, and every scopeless or off-allowlist
# mount — must refuse the machine identity (404, the no-enumeration
# refusal) instead of inheriting a member-style pass.
# #734 turns the hand-copied (method, path) list into a tag-derived check
# (auth/api_scope_surface.py, #678 tool_names.py 同款形态): the closed-loop
# route modules register their routes with API_SCOPE_INTAKE_TAG, the
# authoritative route-name constant lists them, and admission is decided
# from the request's own matched route object — the guard keeps no second
# path copy, so the #631-style drift (a new endpoint shipping while nobody
# syncs the allowlist) is a red contract test instead of a production 404.
# Route-object matching (never path-text) also keeps the {job_id} template
# from shadowing static siblings like /jobs/facets. The surface today:
# submit (POST /runs, dual-checked by the route-level
# require_workspace_api_intake), run status reads, the jobs listing, the
# paginated /jobs/snapshot (codex3 P1: a machine caller must reach the
# WHOLE job status surface past the legacy listing cap), and the three
# #631 external artifact endpoints the intake loop polls after submit
# (status → manifest → raw bytes, routes/external_artifacts.py) — the train
# review (#779 P1-1) found them missing here, which 404'd the machine
# identity before the router.


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
    2. the request's matched route must be on the intake surface
       (tag-derived, see api_scope_surface) — every other route, including
       OTHER GETs (secrets, materials, chat sessions, preview panels,
       metrics) and scopeless mounts, 404s the machine identity.
    """
    if user.get("actor_scope") != WORKSPACE_API_SCOPE:
        return False
    scope = api_scope_route_scope(request)
    bound = user.get("scoped_workspace_id")
    if not scope or bound != scope or not api_scope_route_allowed(request.scope.get("route")):
        raise HTTPException(status_code=404, detail="Workspace not found")
    return True
