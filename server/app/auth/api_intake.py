"""Workspace API intake guard (#626): the runs router's scoped-identity gate.

The ONE effecting surface a workspace API token may take. Split from
workspace_access.py so the membership guard keeps its file budget; the two
modules stay coherent — the machine identity may ONLY submit runs here and
read run/job status (the read allowlist lives in workspace_access).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.exceptions import HTTPException

from server.app.auth.dependencies import get_current_user
from server.app.auth.scoped_tokens import STUDIO_AGENT_SCOPE
from server.app.auth.workspace_access import (
    _workspace_scope,
    require_workspace_access,
)
from server.app.auth.workspace_api_tokens import WORKSPACE_API_SCOPE


def require_workspace_api_intake(
    request: Request,
    user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    """Runs-router POST guard: full sessions take the standard membership
    check; an api-scope machine identity passes only on its OWN workspace
    (this is the ONE effecting surface the intake channel may take — the
    read side is allowlisted in workspace_access); every other scoped
    identity (studio-agent runs included) keeps the 403 that
    ``reject_studio_agent_scope`` used to give this route — the intake
    channel must not become a side door for the studio-agent tool surface.
    """
    scope = user.get("actor_scope")
    if scope == WORKSPACE_API_SCOPE:
        workspace_id = _workspace_scope(request)
        bound = user.get("scoped_workspace_id")
        if not workspace_id or bound != workspace_id:
            raise HTTPException(status_code=404, detail="Workspace not found")
        return user
    if scope:
        detail = (
            "Studio agent scope cannot take effect"
            if scope == STUDIO_AGENT_SCOPE
            else "Scoped tokens cannot take effect"
        )
        raise HTTPException(status_code=403, detail=detail)
    return require_workspace_access(request=request, user=user)
