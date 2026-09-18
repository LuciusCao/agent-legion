from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from server.app.routes.job_artifact_raw import register_raw_artifact_route
from server.app.routes.job_contracts import ArtifactResponse
from server.app.routes.job_http import raise_job_http_error
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
    # 注册顺序敏感：raw 端点必须先于 {artifact_name:path} 注册。
    register_raw_artifact_route(router, service, settings)

    @router.get("/jobs/{job_id}/artifacts/{artifact_name:path}", response_model=ArtifactResponse)
    def get_artifact(job_id: str, artifact_name: str) -> ArtifactResponse:
        # Legacy bare route：scoped/成员/admin 语义由 job_group 的
        # require_job_workspace_access 统一裁决（#745 按 job 行反查授权域；
        # 见 jobs.py get_job 的注释）。
        try:
            return ArtifactResponse(**service.read(job_id, artifact_name))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.get("/jobs/{job_id}/runs/{run_id}/log", response_model=JobLogResponse)
    def get_job_run_log(job_id: str, run_id: int, raw: bool = False):
        # Legacy bare route：同上（#745 job 归属守卫统一裁决）。
        try:
            if raw:
                return PlainTextResponse(
                    log_service.read_raw(job_id, run_id), media_type="text/plain"
                )
            return JobLogResponse(**log_service.read(job_id, run_id))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
