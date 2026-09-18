"""Api-scope machine-identity arm for the workspace membership guards（#626，
rebase 到 #745 后拆自 ``workspace_access``：文件预算——job_id 守卫加高后
两份同型分支再留在原文件必超带）。一个 workspace API intake token
（actor_scope='api'）的全部权限模型是「恰好一个绑定 workspace + 文档化的
intake 面」：本模块只裁决这条窄边界，成员行/角色判定仍归
``workspace_access``。"""

from __future__ import annotations

import re

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
# (method, route template); templates compile to exact full-path patterns
# ({param} → one path segment, no trailing anything) — a prefix or
# substring match here would widen the surface again. POST /runs appears
# here because the job_route_group mounts a membership guard on the whole
# runs router; the route-level require_workspace_api_intake does the
# admission. GET /jobs is the legacy 500-cap listing; codex3 P1 adds the
# paginated /jobs/snapshot (cursor + run_id filter) so a machine caller
# can actually reach the WHOLE job status surface — a run with more items
# than the legacy cap, or a workspace with newer jobs, is otherwise
# unreadable.
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
    2. (method, path) must be on the intake allowlist — every other route,
       including OTHER GETs (secrets, materials, chat sessions, preview
       panels, metrics) and scopeless mounts, 404s the machine identity.
    """
    if user.get("actor_scope") != WORKSPACE_API_SCOPE:
        return False
    scope = api_scope_route_scope(request)
    bound = user.get("scoped_workspace_id")
    if (
        not scope
        or bound != scope
        or not _api_scope_route_allowed(request.method, request.url.path)
    ):
        raise HTTPException(status_code=404, detail="Workspace not found")
    return True
