from fastapi import APIRouter, Depends

from server.app.auth.dependencies import reject_scoped_token_on_bare_job_route
from server.app.routes.job_contracts import ArtifactResponse
from server.app.routes.job_http import raise_job_http_error
from server.app.services.job_artifacts import JobArtifactService
from server.app.services.job_errors import JobServiceError


def create_job_invalid_paths_router(
    service: JobArtifactService,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/jobs/{job_id}/{invalid_path:path}",
        response_model=ArtifactResponse,
        dependencies=[Depends(reject_scoped_token_on_bare_job_route)],
    )
    def reject_invalid_job_subpath(job_id: str, invalid_path: str) -> None:
        # Legacy bare catch-all（#631 攻击审查 H2 收口）：scoped token 404。
        try:
            service.reject_subpath(job_id)
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
