"""Raw artifact bytes serving: media-type whitelist + response construction.

Split from routes/job_artifacts.py for the architecture file budget; the
route registration follows the register_*_route pattern (see
package_clear_packed.py) so the router file stays at its baseline.
"""

from __future__ import annotations

from fastapi import APIRouter, Header
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from server.app.routes.job_artifact_media import (
    FALLBACK_MEDIA_TYPE as _FALLBACK_MEDIA_TYPE,
)
from server.app.routes.job_artifact_media import (
    raw_media_type,
)
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.services.job_artifact_raw import RawArtifact
from server.app.services.job_artifacts import JobArtifactService
from server.app.services.job_errors import JobServiceError
from server.app.settings import Settings

# 对象存储产物按 64 KiB 块输出：StreamingResponse 对同步可迭代对象走
# iterate_in_threadpool，botocore StreamingBody 默认 1 KiB 一块会让大媒体
# 每秒数千次线程池跳转。
_STREAM_CHUNK_BYTES = 64 * 1024


def _is_whitelisted(media_type: str) -> bool:
    return media_type != _FALLBACK_MEDIA_TYPE


def raw_response(raw: RawArtifact) -> FileResponse | StreamingResponse:
    """Build the raw-serving response for a located artifact.

    Local files use FileResponse (native Range support for media seeking);
    object-store streams are served with a 64 KiB chunk iterator and a
    BackgroundTask that closes the stream on both completion and client
    disconnect (starlette runs background tasks after either path). A
    ranged handle (open_raw_artifact with start/end) answers 206 with
    Content-Range so object-store-only media can seek.

    Whitelisted media renders inline; anything else is forced to a download
    on both branches (octet-stream alone doesn't render, the attachment
    disposition keeps direct navigation from trying).
    """
    media_type = raw_media_type(raw.name)
    disposition = "inline" if _is_whitelisted(media_type) else "attachment"
    if raw.stream is not None:
        stream = raw.stream
        headers = {}
        range_start, range_end = raw.range_start, raw.range_end
        if range_start is not None and range_end is not None:
            # 闭区间 → Content-Length；Content-Range 供播放器计算区间。
            headers["Content-Range"] = f"bytes {range_start}-{range_end}/{raw.size_bytes}"
            headers["Content-Length"] = str(range_end - range_start + 1)
            headers["Accept-Ranges"] = "bytes"
        elif raw.size_bytes is not None:
            headers["Content-Length"] = str(raw.size_bytes)
            headers["Accept-Ranges"] = "bytes"
        if disposition == "attachment":
            # artifact 名只挡了路径分隔符；引号/换行会让 header 畸形，
            # 按 RFC 6266 转义（starlette 的 quote 用法见 FileResponse）。
            from urllib.parse import quote

            escaped = quote(raw.name)
            headers["Content-Disposition"] = (
                f"attachment; filename=\"{escaped}\"; filename*=UTF-8''{escaped}"
            )
        status = 206 if raw.range_start is not None and raw.range_end is not None else 200
        return StreamingResponse(
            iter(lambda: stream.read(_STREAM_CHUNK_BYTES), b""),
            status_code=status,
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
    def get_artifact_raw(
        job_id: str,
        artifact_name: str,
        range_header: str | None = Header(default=None, alias="Range"),
    ) -> FileResponse | StreamingResponse:
        require_workflows_enabled(settings)
        # Range 解析在 service.open_raw 内（本地分支忽略，FileResponse 原生支持）。
        try:
            return raw_response(service.open_raw(job_id, artifact_name, range_header))
        except JobServiceError as exc:
            raise_job_http_error(exc)
