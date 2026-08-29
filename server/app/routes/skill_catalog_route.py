from __future__ import annotations

from fastapi import APIRouter

from server.app.db.dialect import ConnectSource
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.routes.skill_contracts import SkillDetailResponse
from server.app.services.job_errors import JobServiceError
from server.app.services.skill_catalog import SkillCatalogService
from server.app.settings import Settings


def create_skill_catalog_router(
    settings: Settings, connect_source: ConnectSource | None = None
) -> APIRouter:
    """``connect_source``: JobQueries facade (or bare DSN) for the skill
    catalog store — BOUNDARY-DATA-001, #187; falls back to the settings DSN."""
    router = APIRouter()
    skills = SkillCatalogService(connect_source or settings.database_url)

    @router.get("/agent-catalog/skills/{skill_key:path}", response_model=SkillDetailResponse)
    def get_skill(skill_key: str, ref: str | None = None) -> SkillDetailResponse:
        require_workflows_enabled(settings)
        try:
            # ref (a git tag of the skill repo) previews that tag's content;
            # an unknown tag is a 404 (see SkillDetailResponse).
            return SkillDetailResponse(**skills.detail(skill_key, ref=ref))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
