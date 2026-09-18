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

from server.app.auth.workspace_api_tokens import WORKSPACE_API_SCOPE


def enforce_workspace_membership(
    request: Request, workspace_id: str | None, user: dict[str, Any]
) -> None:
    if user.get("role") == "admin":
        return
    if workspace_id is None:
        raise HTTPException(status_code=403, detail="Admin role required for global metrics")
    # #626 review hardening (codex P2-2): the api-scope machine identity has
    # no user row — the member lookup below would KeyError (500). It never
    # gets this far anyway (the metrics route is off the intake allowlist,
    # so require_workspace_access already 404'd it); this is defense in
    # depth for that ONE scope only. A studio-agent scoped token carries the
    # initiating user's row: it keeps its pre-#626 read access to the
    # minter's workspaces' metrics via the normal member lookup below.
    if user.get("actor_scope") == WORKSPACE_API_SCOPE:
        raise HTTPException(status_code=404, detail="Workspace not found")
    role = request.app.state.job_db.get_workspace_role(workspace_id, str(user["id"]))
    if role is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
