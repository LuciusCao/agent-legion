from fastapi import APIRouter

from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.routes.skill_contracts import SkillDetailResponse
from server.app.services.job_errors import JobServiceError
from server.app.services.skill_catalog import SkillCatalogService
from server.app.settings import Settings


def create_skill_catalog_router(settings: Settings) -> APIRouter:
    router = APIRouter()
    skills = SkillCatalogService(settings.database_url)

    @router.get("/executors/skills/{skill_key:path}", response_model=SkillDetailResponse)
    def get_skill(skill_key: str) -> SkillDetailResponse:
        require_workflows_enabled(settings)
        try:
            return SkillDetailResponse(**skills.detail(skill_key))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
