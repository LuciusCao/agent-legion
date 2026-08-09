from fastapi import APIRouter

from server.app.executors._pi_skill import build_skill_manager
from server.app.routes.job_http import require_workflows_enabled
from server.app.routes.skill_contracts import (
    SkillTagsResponse,
    SkillValidateRequest,
    SkillValidateResponse,
)
from server.app.services.skill_validator import SkillValidator
from server.app.settings import Settings


def create_skills_router(settings: Settings) -> APIRouter:
    """Skill path validation + tag discovery for the Studio Agent editor."""
    router = APIRouter()

    def _validator() -> SkillValidator:
        manager = build_skill_manager(settings.database_url)
        return SkillValidator(manager.base_dir, manager.load_lock)

    @router.post("/skills/validate", response_model=SkillValidateResponse)
    def validate_skill(request: SkillValidateRequest) -> SkillValidateResponse:
        require_workflows_enabled(settings)
        result = _validator().validate(request.path)
        return SkillValidateResponse(
            valid=result.valid,
            path=result.path,
            skill_key=result.skill_key,
            error=result.error,
            tags=list(result.tags),
            latest_tag=result.latest_tag,
            locked_ref=result.locked_ref,
        )

    @router.get("/skills/tags", response_model=SkillTagsResponse)
    def list_skill_tags(path: str) -> SkillTagsResponse:
        require_workflows_enabled(settings)
        result = _validator().list_tags(path)
        return SkillTagsResponse(
            path=result.path, tags=list(result.tags), latest_tag=result.latest_tag
        )

    return router
