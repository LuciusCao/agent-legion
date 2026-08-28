"""Raw artifact bytes serving: media-type whitelist + response construction.

Split from routes/job_artifacts.py for the architecture file budget; the
route registration follows the register_*_route pattern (see
package_clear_packed.py) so the router file stays at its baseline.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.services.job_artifact_raw import RawArtifact
from server.app.services.job_artifacts import JobArtifactService
from server.app.services.job_errors import JobServiceError
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

# 对象存储产物按 64 KiB 块输出：StreamingResponse 对同步可迭代对象走
# iterate_in_threadpool，botocore StreamingBody 默认 1 KiB 一块会让大媒体
# 每秒数千次线程池跳转。
_STREAM_CHUNK_BYTES = 64 * 1024


def raw_media_type(artifact_name: str) -> str:
    """Map an artifact filename to a servable media type (whitelist-gated)."""
    for suffix, media_type in RAW_MEDIA_TYPES.items():
        if artifact_name.lower().endswith(suffix):
            return media_type
    return _FALLBACK_MEDIA_TYPE


def _is_whitelisted(media_type: str) -> bool:
    return media_type != _FALLBACK_MEDIA_TYPE


def raw_response(raw: RawArtifact) -> FileResponse | StreamingResponse:
    """Build the raw-serving response for a located artifact.

    Local files use FileResponse (native Range support for media seeking);
    object-store streams are served whole-body with a 64 KiB chunk iterator
    and a BackgroundTask that closes the stream on both completion and client
    disconnect (starlette runs background tasks after either path).
    """
    media_type = raw_media_type(raw.name)
    # 白名单内联渲染；白名单外强制 attachment 下载（双保险：octet-stream
    # 本身不渲染，disposition 保证浏览器导航到该 URL 也只会下载）。
    disposition = "inline" if _is_whitelisted(media_type) else "attachment"
    if raw.stream is not None:
        stream = raw.stream
        headers = {}
        if raw.size_bytes is not None:
            headers["Content-Length"] = str(raw.size_bytes)
        return StreamingResponse(
            iter(lambda: stream.read(_STREAM_CHUNK_BYTES), b""),
            media_type=media_type,
            headers=headers,
            background=BackgroundTask(stream.close),
        )
    # open_raw 的非 stream 分支保证 path 非 None。
    assert raw.path is not None
    return FileResponse(
        raw.path,
        media_type=media_type,
        filename=raw.name,
        content_disposition_type=disposition,
    )


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
    def get_artifact_raw(job_id: str, artifact_name: str) -> FileResponse | StreamingResponse:
        require_workflows_enabled(settings)
        try:
            return raw_response(service.open_raw(job_id, artifact_name))
        except JobServiceError as exc:
            raise_job_http_error(exc)
