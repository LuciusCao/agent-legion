"""Raw artifact response construction: media-type whitelist + stream serving.

Split from ``routes/job_artifact_raw`` for the file-size budget: the route
module keeps only the registration, this module owns the response builders
shared by the local-file and object-store branches.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import BinaryIO

from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from server.app.services.job_artifact_media import (
    attachment_disposition,
    raw_disposition_type,
    raw_media_type,
)
from server.app.services.job_artifact_raw import RawArtifact

# 对象存储产物按 64 KiB 块输出：StreamingResponse 对同步可迭代对象走
# iterate_in_threadpool，botocore StreamingBody 默认 1 KiB 一块会让大媒体
# 每秒数千次线程池跳转。
_STREAM_CHUNK_BYTES = 64 * 1024


def _decoded_range_chunks(stream: BinaryIO, start: int, end: int) -> Iterator[bytes]:
    """全量解压流的 [start, end] 闭区间分块（#1259 unsliced_stream）。

    gzip 顺序解码，先跳读 start 字节再限量供给；对象损坏（流短于 trailer
    ISIZE 宣称的解压长度）时提前结束——响应体短于 Content-Length，客户端
    按截断处理。
    """
    to_skip = start
    while to_skip > 0 and (skipped := stream.read(min(to_skip, _STREAM_CHUNK_BYTES))):
        to_skip -= len(skipped)
    remaining = end - start + 1
    while remaining > 0 and (chunk := stream.read(min(remaining, _STREAM_CHUNK_BYTES))):
        remaining -= len(chunk)
        yield chunk


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
    Content-Length is the stored size. This passthrough form never combines
    with a Range request (#1259): ranged ``.gz`` requests arrive as
    decompressed-unit handles (sliced below via ``unsliced_stream``), so
    Accept-Ranges stays off only this form.

    ``raw.unsatisfiable_range`` (#1259): the requested range lies beyond the
    representation — 416 with ``Content-Range: bytes */<total>``, empty body.
    """
    media_type = raw_media_type(raw.name)
    disposition = raw_disposition_type(raw.name)
    if raw.unsatisfiable_range:
        total = "*" if raw.size_bytes is None else str(raw.size_bytes)
        return StreamingResponse(
            iter(()),
            status_code=416,
            media_type=media_type,
            headers={"Content-Range": f"bytes */{total}"},
        )
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
            headers["Content-Disposition"] = attachment_disposition(raw.name)
        status = 206 if raw.range_start is not None and raw.range_end is not None else 200
        body: Iterator[bytes] = iter(lambda: stream.read(_STREAM_CHUNK_BYTES), b"")
        if raw.unsliced_stream and range_start is not None and range_end is not None:
            body = _decoded_range_chunks(stream, range_start, range_end)
        return StreamingResponse(
            body,
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
