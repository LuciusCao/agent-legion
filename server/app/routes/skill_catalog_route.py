from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from server.app.auth.dependencies import get_current_user
from server.app.db.dialect import ConnectSource
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.skill_contracts import SkillDetailResponse
from server.app.services.job_errors import JobServiceError
from server.app.services.skill_catalog import SkillCatalogService
from server.app.settings import Settings


def require_skill_workspace_member(
    request: Request,
    workspace_id: str,
    user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> None:
    """Membership check for skill reads keyed by ``<workspace>/<capability>``.

    The route carries no ``workspace_id`` path parameter, so the generic
    ``require_workspace_access`` mount passes any logged-in user through —
    the same blind spot the job routes had (#710); a low-privilege account
    could read any workspace's full skill content (red-team V1 on #710's
    audit: skills are the prompts/contracts/scripts IP). Non-members get
    404 "Skill not found", indistinguishable from an unknown skill key —
    enumeration-safe like every other workspace-scoped refusal.
    """
    if user.get("role") == "admin":
        return
    role = request.app.state.job_db.get_workspace_role(workspace_id, str(user["id"]))
    if role is None:
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
        user: Annotated[dict[str, Any], Depends(get_current_user)],
        ref: str | None = None,
    ) -> SkillDetailResponse:
        workspace_id = skill_key.partition("/")[0]
        require_skill_workspace_member(request, workspace_id, user)
        try:
            # ref (a git tag of the skill repo) previews that tag's content;
            # an unknown tag is a 404 (see SkillDetailResponse).
            return SkillDetailResponse(**_skills().detail(skill_key, ref=ref))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
