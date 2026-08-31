from fastapi import APIRouter

from server.app.jobs import JobQueries
from server.app.routes.job_http import require_workflows_enabled
from server.app.routes.skill_directories_contracts import SkillDirectoriesResponse
from server.app.services.skill_browser import SkillBrowser
from server.app.settings import Settings
from server.app.skills.runtime import build_skill_manager


def create_skill_directories_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    """Read-only candidate skill directory listing for the Studio skill picker (#327)."""
    router = APIRouter()

    def _browser() -> SkillBrowser:
        manager = build_skill_manager(job_db, settings.skills_runs_dir)
        return SkillBrowser(manager.base_dir)

    @router.get("/skills/directories", response_model=SkillDirectoriesResponse)
    def list_skill_directories(workspace_id: str) -> SkillDirectoriesResponse:
        # The ``workspace_id`` query-param name is load-bearing:
        # require_workspace_access reads it and rejects non-members (404)
        # before this handler runs.
        require_workflows_enabled(settings)
        directories = _browser().list_directories(workspace_id)
        return SkillDirectoriesResponse(workspace_id=workspace_id, directories=list(directories))

    return router
