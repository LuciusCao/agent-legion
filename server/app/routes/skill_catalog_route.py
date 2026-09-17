from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from server.app.auth.dependencies import get_current_user
from server.app.db.dialect import ConnectSource
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.skill_contracts import SkillDetailResponse
from server.app.services.job_errors import JobServiceError
from server.app.services.skill_catalog import SkillCatalogService
from server.app.settings import Settings


def require_skill_scope_binding(
    workspace_id: str,
    user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> None:
    """Scoped-token binding for the query-scoped skill surfaces (#710,
    codex P1 on #745).

    The router-level ``require_workspace_access`` already membership-checks
    the ``workspace_id`` query parameter (non-members get a uniform
    workspace-shaped 404) — what it does not do is honor a scoped token's
    workspace binding, so a run token minted for workspace A (even by an
    admin, who is a member everywhere) could read workspace B's skills
    through these routes. The binding runs regardless of role: scoped tokens
    inherit the minter's role, so admin-minted bindings stay bound too
    (same shape as require_job_workspace_access)."""
    bound = user.get("scoped_workspace_id")
    if bound and str(bound) != str(workspace_id):
        # Same detail as the router guard's refusal: a differing string
        # would let callers distinguish refusal reasons.
        raise HTTPException(status_code=404, detail="Workspace not found")


def require_skill_key_in_workspace(request: Request, skill_key: str, workspace_id: str) -> None:
    """Workspace-directory strictness for skill keys (#710, codex P2 on #745).

    A skill key's first segment is a GROUP name, not necessarily a workspace
    id — the demo ships group ``education-video-problems-generation`` under
    workspace ``education_video_problems_generation``, so ownership cannot
    be inferred from the key alone. When the first segment IS an existing
    workspace's id (the create_skill layout ``<workspace_id>/<name>``), the
    key belongs to that workspace: members of other workspaces get 404 even
    through their own scope. Group directories (first segment not a
    workspace id) stay shared read surfaces authorized by workspace
    membership alone."""
    key_workspace = skill_key.partition("/")[0]
    if key_workspace == str(workspace_id):
        return
    if request.app.state.job_db.get_workspace(key_workspace) is not None:
        raise HTTPException(status_code=404, detail="Skill not found")


def create_skill_catalog_router(
    settings: Settings, connect_source: ConnectSource | None = None
) -> APIRouter:
    """``connect_source``: JobQueries facade (or bare DSN) for the skill
    catalog store — BOUNDARY-DATA-001, #187; falls back to the settings DSN."""
    router = APIRouter()

    def _skills() -> SkillCatalogService:
        # Per-request build: base_dir resolves at call time (HOME may be
        # monkeypatched in tests, same laziness as the skills router).
        return SkillCatalogService(connect_source or settings.database_url)

    @router.get("/agent-catalog/skills/{skill_key:path}", response_model=SkillDetailResponse)
    def get_skill(
        skill_key: str,
        request: Request,
        workspace_id: Annotated[str, Query()],
        user: Annotated[dict[str, Any], Depends(get_current_user)],
        ref: str | None = None,
    ) -> SkillDetailResponse:
        # The query parameter doubles as the membership scope for the
        # router-level guard (same pattern as the /agent-catalog list
        # endpoint); the binding + directory checks here cover what that
        # guard cannot see.
        require_skill_scope_binding(workspace_id, user)
        require_skill_key_in_workspace(request, skill_key, workspace_id)
        try:
            # ref (a git tag of the skill repo) previews that tag's content;
            # an unknown tag is a 404 (see SkillDetailResponse).
            return SkillDetailResponse(**_skills().detail(skill_key, ref=ref))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
