"""Studio-agent skill tool endpoints (issue #217).

Skill read/validate/save-version for the built-in Studio authoring agent.
Skills are instance-level, so these endpoints sit on the global tool
router (no workspace binding) while still requiring a studio-agent
scoped token. ``save_skill_version`` is draft-only by design: it commits
and tags the skill's LOCAL source repo but never touches the DB skill
lock — publishing (ref change + relock) stays a human admin action.
"""

from fastapi import APIRouter

from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.routes.skill_contracts import SkillDetailResponse
from server.app.routes.studio_agent_skill_contracts import (
    SkillSaveVersionRequest,
    SkillSaveVersionResponse,
    SkillValidateToolResponse,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.skill_catalog import SkillCatalogService
from server.app.services.skill_editing import SkillEditingService, SkillFileWrite
from server.app.services.skill_source_store import SkillSourceStore
from server.app.settings import Settings


def create_studio_agent_skill_tools_router(settings: Settings) -> APIRouter:
    router = APIRouter()
    catalog = SkillCatalogService(settings.database_url)
    editing = SkillEditingService(SkillSourceStore(settings.database_url))

    @router.get("/studio-agent/tools/skills/{skill_key:path}", response_model=SkillDetailResponse)
    def get_skill(skill_key: str, ref: str | None = None) -> SkillDetailResponse:
        require_workflows_enabled(settings)
        try:
            return SkillDetailResponse(**catalog.detail(skill_key, ref=ref))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.post(
        "/studio-agent/tools/skills/{skill_key:path}/validate",
        response_model=SkillValidateToolResponse,
    )
    def validate_skill(skill_key: str) -> SkillValidateToolResponse:
        require_workflows_enabled(settings)
        try:
            return SkillValidateToolResponse(**editing.validate(skill_key))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.post(
        "/studio-agent/tools/skills/{skill_key:path}/versions",
        response_model=SkillSaveVersionResponse,
        status_code=201,
    )
    def save_skill_version(
        skill_key: str, payload: SkillSaveVersionRequest
    ) -> SkillSaveVersionResponse:
        require_workflows_enabled(settings)
        files = [SkillFileWrite(path=item.path, content=item.content) for item in payload.files]
        try:
            result = editing.save_version(skill_key, files, payload.new_tag, payload.message)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return SkillSaveVersionResponse(**result)

    return router
