from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from server.app.routes.job_contracts import ArtifactResponse
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.routes.job_view_contracts import JobLogResponse
from server.app.services.job_artifacts import JobArtifactService
from server.app.services.job_errors import JobServiceError
from server.app.services.job_logs import JobLogService
from server.app.settings import Settings


def create_job_artifacts_router(
    service: JobArtifactService,
    settings: Settings,
    log_service: JobLogService,
) -> APIRouter:
    router = APIRouter()

    @router.get("/jobs/{job_id}/artifacts/{artifact_name:path}", response_model=ArtifactResponse)
    def get_artifact(job_id: str, artifact_name: str) -> ArtifactResponse:
        require_workflows_enabled(settings)
        try:
            return ArtifactResponse(**service.read(job_id, artifact_name))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.get("/jobs/{job_id}/runs/{run_id}/log", response_model=JobLogResponse)
    def get_job_run_log(job_id: str, run_id: int, raw: bool = False):
        require_workflows_enabled(settings)
        try:
            if raw:
                return PlainTextResponse(
                    log_service.read_raw(job_id, run_id), media_type="text/plain"
                )
            return JobLogResponse(**log_service.read(job_id, run_id))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
