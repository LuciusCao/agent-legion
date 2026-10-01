"""External artifact-access routes (#631).

Workspace-prefixed read surface for external systems running the
submit → poll status → download artifacts loop (#626 submits, this reads):

    GET /api/workspaces/{workspace_id}/jobs/{job_id}                      status
    GET /api/workspaces/{workspace_id}/jobs/{job_id}/artifacts            manifest
    GET /api/workspaces/{workspace_id}/jobs/{job_id}/artifacts/{artifact_name}/raw bytes

The router mounts inside job_group (routes/__init__.py), so every endpoint
passes require_workspace_access (Bearer channel, no CSRF — the #626 workspace
API token plugs in unchanged); the explicit job.workspace_id comparison in the
service covers what the path-param guard cannot see on /jobs/{job_id}-shaped
routes: a job id from another workspace is a 404, not a 403 (no enumeration).
require_scoped_workspace_match adds the scope-aware half (#631 review P1):
require_workspace_access checks only the minting user's role/membership, so
without it a Bearer token bound to scoped_workspace_id=ws-a could read through
every workspace that user can see — mismatches stay 404, keeping the surface's
no-enumeration semantics.

Split from the studio-agent tool surface (#329): this is member/token-facing
observation data for machines, not an agent loop.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from fastapi.responses import FileResponse, StreamingResponse

from server.app.auth.workspace_access import require_scoped_workspace_match
from server.app.routes.external_artifact_contracts import (
    ExternalArtifactListResponse,
    ExternalJobStatusResponse,
)
from server.app.routes.job_artifact_raw_response import raw_response
from server.app.routes.job_http import raise_job_http_error
from server.app.services.external_artifact_access import ExternalArtifactAccessService
from server.app.services.job_errors import JobServiceError


def create_external_artifact_router(
    access_service: ExternalArtifactAccessService,
) -> APIRouter:
    # 整组同守卫（#631 review P1）：三端点都拿 workspace_id 路径参数，scoped
    # token 的绑定检查属于整组而非单端点；job_group 的 require_workspace_
    # access 仍然先行（成员 404 / 角色检查）。
    router = APIRouter(dependencies=[Depends(require_scoped_workspace_match)])

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

    # {artifact_name:path}（#631 review P2-1）：声明产物名可含 /（reports/
    # final.json——Worker 解包与 promote 都保留子目录），单段参数连
    # URL 编码的斜杠也匹配不上，manifest 列出的名字无法下载。/raw 后缀
    # 在 :path 贪婪匹配下取自尾部（fastapi 的 path convertor 按后缀截
    # 断），名字里的 / 不再破坏匹配；open_raw_current 内做安全相对路径
    # 校验（绝对名/.. 段 400）。
    # #703 codex round 4 (P2-2)：Range 请求时 raw_response 实际答 206
    # （对象分支带 Content-Range），路由契约此前只声明 200——生成客户端
    # 把分段下载当异常。206 与 200 共用同一 octet-stream 响应体描述
    # （含 Content-Range/Content-Length 头），本地 FileResponse 分支由
    # starlette 原生处理两种状态码。
    @router.get(
        "/workspaces/{workspace_id}/jobs/{job_id}/artifacts/{artifact_name:path}/raw",
        response_class=FileResponse,
        response_model=None,
        responses={
            200: {"content": {"application/octet-stream": {}}},
            206: {
                "description": "Partial Content (Range request)",
                "content": {"application/octet-stream": {}},
                "headers": {
                    "Content-Range": {"schema": {"type": "string"}},
                    "Content-Length": {"schema": {"type": "string"}},
                },
            },
        },
    )
    def get_external_artifact_raw(
        workspace_id: str,
        job_id: str,
        artifact_name: str,
        range_header: str | None = Header(default=None, alias="Range"),
    ) -> FileResponse | StreamingResponse:
        # 归属校验（404 防枚举）与本地子路径下载门（#703 codex round 4
        # P2-1：未声明 outputs 的名字不可下载）都在 access_service：
        # JobArtifactService 的 open_raw_current 只查 job 存在性，不知道
        # workspace 语境。
        try:
            # P2-2：有 manifest 行时优先对象副本——清单刚刚把行的 content_
            # hash/uploaded_at 当作当前结果公布，本地 job_dir 缓存可能滞后
            # （rerun 替换本地文件、重传/登记行未落地）；无行才回落本地。
            return raw_response(
                access_service.open_raw_current(workspace_id, job_id, artifact_name, range_header)
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    return router
