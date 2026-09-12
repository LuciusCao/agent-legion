"""Studio-agent skill creation tool endpoint (#633).

``create_skill`` materializes a brand-new skill repo under the CALLING
workspace's skill directory (``~/.agents/skills/<workspace_id>/<name>``),
so it rides the workspace-scoped sub-router: a session-bound run token
(schema v45) cannot create skills under a foreign workspace. Draft-only by
design (STUDIO-AGENT-001): the initial commit + tag land in the local
in-place repo only — the DB skill lock is never touched and nothing is
published (re-pin/relock stays a human admin action), exactly like
``save_skill_version``.
"""

from fastapi import APIRouter

from server.app.jobs import JobQueries
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.studio_agent_skill_contracts import (
    SkillCreateRequest,
    SkillCreateResponse,
)
from server.app.services.job_errors import JobServiceError
from server.app.services.skill_creation import SkillCreationService
from server.app.services.skill_editing import SkillFileWrite
from server.app.settings import Settings


def create_studio_agent_skill_creation_tools_router(
    job_db: JobQueries, settings: Settings
) -> APIRouter:
    router = APIRouter()
    creation = SkillCreationService(job_db, runs_dir=settings.skills_runs_dir)

    @router.post(
        "/studio-agent/tools/workspaces/{workspace_id}/skills",
        response_model=SkillCreateResponse,
        status_code=201,
    )
    def create_skill(workspace_id: str, payload: SkillCreateRequest) -> SkillCreateResponse:
        files = [SkillFileWrite(path=item.path, content=item.content) for item in payload.files]
        try:
            result = creation.create_skill(
                workspace_id, payload.skill_name, files, payload.new_tag, payload.message
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return SkillCreateResponse(**result)

    return router
