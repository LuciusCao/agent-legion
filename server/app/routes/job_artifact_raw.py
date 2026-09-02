"""Raw artifact bytes serving route registration.

Split from routes/job_artifacts.py for the architecture file budget; the
route registration follows the register_*_route pattern (see
package_clear_packed.py) so the router file stays at its baseline. The
response builders live in ``job_artifact_raw_response`` (same split).
"""

from __future__ import annotations

from fastapi import APIRouter, Header
from fastapi.responses import FileResponse, StreamingResponse

from server.app.routes.job_artifact_media import raw_media_type
from server.app.routes.job_artifact_raw_response import raw_response
from server.app.routes.job_http import raise_job_http_error
from server.app.services.job_artifacts import JobArtifactService
from server.app.services.job_errors import JobServiceError
from server.app.settings import Settings

__all__ = ["raw_media_type", "raw_response", "register_raw_artifact_route"]


def register_raw_artifact_route(
    router: APIRouter,
    service: JobArtifactService,
    settings: Settings,
) -> None:
    # raw 必须先于 {artifact_name:path} 注册（在 job_artifacts.py 的
    # create_job_artifacts_router 里调用），否则 "foo.json/raw" 会被吞成
    # 名为 "foo.json/raw" 的 artifact 查询。
    @router.get(
        "/jobs/{job_id}/artifacts/{artifact_name}/raw",
        response_class=FileResponse,
        response_model=None,
        responses={200: {"content": {"application/octet-stream": {}}}},
    )
    def get_artifact_raw(
        job_id: str,
        artifact_name: str,
        range_header: str | None = Header(default=None, alias="Range"),
    ) -> FileResponse | StreamingResponse:
        # Range 解析在 service.open_raw 内（本地分支忽略，FileResponse 原生支持）。
        try:
            return raw_response(service.open_raw(job_id, artifact_name, range_header))
        except JobServiceError as exc:
            raise_job_http_error(exc)
