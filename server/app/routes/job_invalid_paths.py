from fastapi import APIRouter

from server.app.routes.job_contracts import ArtifactResponse
from server.app.routes.job_http import raise_job_http_error
from server.app.services.job_artifacts import JobArtifactService
from server.app.services.job_errors import JobServiceError


def create_job_invalid_paths_router(
    service: JobArtifactService,
) -> APIRouter:
    router = APIRouter()

    @router.get("/jobs/{job_id}/{invalid_path:path}", response_model=ArtifactResponse)
    def reject_invalid_job_subpath(job_id: str, invalid_path: str) -> None:
        # Legacy bare catch-all：scoped/成员/admin 语义由 job_group 的
        # require_job_workspace_access 统一裁决（#745 job 归属守卫）。
        try:
            service.reject_subpath(job_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
