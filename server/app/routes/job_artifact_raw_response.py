"""Raw artifact response construction: media-type whitelist + stream serving.

Split from ``routes/job_artifact_raw`` for the file-size budget: the route
module keeps only the registration, this module owns the response builders
shared by the local-file and object-store branches.
"""

from __future__ import annotations

from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from server.app.routes.job_artifact_media import (
    FALLBACK_MEDIA_TYPE as _FALLBACK_MEDIA_TYPE,
)
from server.app.routes.job_artifact_media import (
    raw_media_type,
)
from server.app.services.job_artifact_raw import RawArtifact

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

    ``raw.content_encoding == "gzip"`` (#338): the stream carries the stored
    (compressed) bytes — passed through with a ``Content-Encoding: gzip``
    header so browsers decode natively and API clients gunzip themselves.
    Content-Length is the stored size; Range was already suppressed upstream
    (gzip streams do not decode by byte range), so Accept-Ranges stays off
    this form.
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
        elif raw.size_bytes is not None:
            headers["Content-Length"] = str(raw.size_bytes)
        if raw.content_encoding is not None:
            headers["Content-Encoding"] = raw.content_encoding
        else:
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
