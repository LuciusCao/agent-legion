from fastapi import APIRouter

from server.app.routes.job_contracts import ArtifactResponse
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.services.job_artifacts import JobArtifactService
from server.app.services.job_errors import JobServiceError
from server.app.settings import Settings


def create_job_invalid_paths_router(
    service: JobArtifactService,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.get("/jobs/{job_id}/{invalid_path:path}", response_model=ArtifactResponse)
    def reject_invalid_job_subpath(job_id: str, invalid_path: str) -> None:
        require_workflows_enabled(settings)
        try:
            service.reject_subpath(job_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
