"""External artifact-access routes (#631).

Workspace-prefixed read surface for external systems running the
submit → poll status → download artifacts loop (#626 submits, this reads):

    GET /api/workspaces/{workspace_id}/jobs/{job_id}                      status
    GET /api/workspaces/{workspace_id}/jobs/{job_id}/artifacts            manifest
    GET /api/workspaces/{workspace_id}/jobs/{job_id}/artifacts/{name}/raw bytes

The router mounts inside job_group (routes/__init__.py), so every endpoint
passes require_workspace_access (Bearer channel, no CSRF — the #626 workspace
API token plugs in unchanged); the explicit job.workspace_id comparison in the
service covers what the path-param guard cannot see on /jobs/{job_id}-shaped
routes: a job id from another workspace is a 404, not a 403 (no enumeration).

Split from the studio-agent tool surface (#329): this is member/token-facing
observation data for machines, not an agent loop.
"""

from __future__ import annotations

from fastapi import APIRouter, Header
from fastapi.responses import FileResponse, StreamingResponse

from server.app.routes.external_artifact_contracts import (
    ExternalArtifactListResponse,
    ExternalJobStatusResponse,
)
from server.app.routes.job_artifact_raw_response import raw_response
from server.app.routes.job_http import raise_job_http_error
from server.app.services.external_artifact_access import ExternalArtifactAccessService
from server.app.services.job_artifacts import JobArtifactService
from server.app.services.job_errors import JobServiceError


def create_external_artifact_router(
    access_service: ExternalArtifactAccessService,
    artifact_service: JobArtifactService,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/workspaces/{workspace_id}/jobs/{job_id}",
        response_model=ExternalJobStatusResponse,
    )
    def get_external_job_status(workspace_id: str, job_id: str) -> ExternalJobStatusResponse:
        try:
            return ExternalJobStatusResponse(**access_service.status(workspace_id, job_id))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.get(
        "/workspaces/{workspace_id}/jobs/{job_id}/artifacts",
        response_model=ExternalArtifactListResponse,
    )
    def list_external_artifacts(workspace_id: str, job_id: str) -> ExternalArtifactListResponse:
        try:
            return ExternalArtifactListResponse(
                **access_service.list_artifacts(workspace_id, job_id)
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    # raw 必须先于任何 {artifact_name:path} 形态注册（见 job_artifacts.py 的
    # 注册顺序说明）；本路由没有 path 形态，此注释只为防止未来追加时踩坑。
    @router.get(
        "/workspaces/{workspace_id}/jobs/{job_id}/artifacts/{artifact_name}/raw",
        response_class=FileResponse,
        response_model=None,
        responses={200: {"content": {"application/octet-stream": {}}}},
    )
    def get_external_artifact_raw(
        workspace_id: str,
        job_id: str,
        artifact_name: str,
        range_header: str | None = Header(default=None, alias="Range"),
    ) -> FileResponse | StreamingResponse:
        # 归属校验（404 防枚举）在 access_service：JobArtifactService 的
        # open_raw 只查 job 存在性，不知道 workspace 语境，跨 workspace 的
        # job_id 必须在这里显式比对归属后才允许读。
        try:
            access_service.require_job_in_workspace(workspace_id, job_id)
            return raw_response(artifact_service.open_raw(job_id, artifact_name, range_header))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
