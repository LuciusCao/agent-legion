from fastapi import APIRouter
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

from server.app.routes.job_contracts import ArtifactResponse
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.routes.job_view_contracts import JobLogResponse
from server.app.services.job_artifacts import JobArtifactService, RawArtifact
from server.app.services.job_errors import JobServiceError
from server.app.services.job_logs import JobLogService
from server.app.settings import Settings

# raw 端点的 content-type 白名单即安全边界：仅可内嵌渲染的媒体扩展名映射
# 真实 media type，其余（含 .html/.svg——同源渲染即脚本执行面）一律
# application/octet-stream 强制下载，不经浏览器渲染引擎。
RAW_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".pdf": "application/pdf",
}

_FALLBACK_MEDIA_TYPE = "application/octet-stream"


def raw_media_type(artifact_name: str) -> str:
    """Map an artifact filename to a servable media type (whitelist-gated)."""
    for suffix, media_type in RAW_MEDIA_TYPES.items():
        if artifact_name.lower().endswith(suffix):
            return media_type
    return _FALLBACK_MEDIA_TYPE


def _raw_response(raw: RawArtifact) -> FileResponse | StreamingResponse:
    media_type = raw_media_type(raw.name)
    if raw.stream is not None:
        headers = {}
        if raw.size_bytes is not None:
            headers["Content-Length"] = str(raw.size_bytes)
        # 对象存储产物整流输出；Range seeking 首版不支持（本地 job_dir 产物
        # 走 FileResponse 自带 Range 支持）。
        return StreamingResponse(raw.stream, media_type=media_type, headers=headers)
    # open_raw 的非 stream 分支保证 path 非 None。
    assert raw.path is not None
    return FileResponse(raw.path, media_type=media_type, filename=raw.name)


def create_job_artifacts_router(
    service: JobArtifactService,
    settings: Settings,
    log_service: JobLogService,
) -> APIRouter:
    router = APIRouter()

    # 注册顺序敏感：raw 端点必须先于 {artifact_name:path} 注册，否则
    # "foo.json/raw" 会被 :path 路由吞成名为 "foo.json/raw" 的 artifact 查询。
    @router.get(
        "/jobs/{job_id}/artifacts/{artifact_name}/raw",
        response_class=FileResponse,
        responses={200: {"content": {"application/octet-stream": {}}}},
    )
    def get_artifact_raw(job_id: str, artifact_name: str):
        require_workflows_enabled(settings)
        try:
            return _raw_response(service.open_raw(job_id, artifact_name))
        except JobServiceError as exc:
            raise_job_http_error(exc)

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
