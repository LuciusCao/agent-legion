"""Raw (binary) artifact location: local job_dir file or object-store stream.

Split from services/job_artifacts.py for the architecture file budget; the
text read path stays in JobArtifactService.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from server.app.http_range import parse_range_header
from server.app.services.job_artifact_gzip import is_gzip_key
from server.app.services.job_artifact_objects import (
    JobArtifactObjectStore,
    refuse_row_outside_job_prefix,
)
from server.app.services.job_artifact_raw_types import RawArtifact
from server.app.services.job_errors import NotFoundError

__all__ = ["RawArtifact", "open_raw_artifact", "open_raw_row"]

logger = logging.getLogger(__name__)


def open_raw_row(
    store: JobArtifactObjectStore,
    row: dict[str, Any],
    artifact_name: str,
    range_header: str | None = None,
    *,
    job: dict[str, Any] | None = None,
) -> RawArtifact:
    """Open the object a manifest row points at (#631 external access).

    The object branch of ``open_raw_artifact`` split out for callers that
    resolve the row themselves (manifest-first reads): same semantics —
    ``.gz`` objects pass stored bytes through, others honour the parsed
    single range, storage failures degrade to NotFoundError.

    ``job``（#631 攻击复审 H1）：行所属 job 的记录行（``get_job`` 的
    返回形状），传入时先做读侧 storage_key 前缀兜底
    （``refuse_row_outside_job_prefix``——行是对象读路径的唯一权威，
    写歪的行不读穿 workspace 边界，404 语义）；None 表示调用方无法提
    供 job 语境（测试桩直连），生产调用链都传入。
    """
    if job is not None and refuse_row_outside_job_prefix(row, job):
        raise NotFoundError("Artifact not found")
    size = row.get("size_bytes")
    size_bytes = int(size) if isinstance(size, int) else None
    gzipped = is_gzip_key(str(row["storage_key"]))
    # .gz 对象：Range 失效（open_raw_artifact docstring），永远全量透传存储字节。
    byte_range = None if gzipped else parse_range_header(range_header, size_bytes)
    try:
        if gzipped:
            stream = store.open_object_stream(row)
        elif byte_range is not None:
            stream = store.open_range_stream(row, *byte_range)
        else:
            stream = store.open_stream(row)
    except (ClientError, BotoCoreError) as exc:
        # #204: the store's open/open_range surface is the boto3 data
        # plane (S3StorageClient) — its declared failure family is
        # ClientError (a missing/lifecycle-deleted object surfaces as
        # NoSuchKey) plus BotoCoreError (endpoint unreachable). Both
        # degrade to NotFoundError per the read() contract; anything else
        # is a programming error and surfaces as 500.
        logger.warning(
            "failed to open raw artifact %s of job %s from object storage",
            artifact_name,
            row.get("job_id"),
            exc_info=True,
        )
        raise NotFoundError("Artifact not found") from exc
    return RawArtifact(
        name=artifact_name,
        stream=stream,
        size_bytes=size_bytes,
        range_start=byte_range[0] if byte_range else None,
        range_end=byte_range[1] if byte_range else None,
        content_encoding="gzip" if gzipped else None,
    )


def open_raw_artifact(
    artifact_path: Path,
    object_store: JobArtifactObjectStore | None,
    job_id: str,
    artifact_name: str,
    range_header: str | None = None,
    *,
    manifest_first: bool = False,
    job: dict[str, Any] | None = None,
) -> RawArtifact:
    """Locate a binary-servable artifact: local job_dir file first, then the
    object-store stream (mirrors the text read()'s ordering).

    range_header 解析为单区间后走对象存储的 ranged read（本地分支忽略
    区间——FileResponse 自行处理 Range 头）。存储故障按 NotFoundError
    降级，与 read() 一致；消费（与关闭）返回句柄是调用方责任。

    ``manifest_first``（#631 review P2-2）：先查 manifest 行再考虑本地
    文件——清单公布方（external access）要求下载字节与刚公布的行
    （content_hash/uploaded_at）一致，本地缓存可能滞后。行缺失时回到
    本地优先序（legacy 本地产物）。单次 lookup，无窗口竞态。

    ``job``（#631 攻击复审 H1）：传入时对象分支先做 storage_key 前缀
    兜底（见 ``open_raw_row``）。

    #338 双形态：``.gz`` 对象按存储字节透传（``content_encoding="gzip"``，
    路由层加 Content-Encoding 响应头），manifest ``size_bytes`` 是压缩后
    字节数（与透传 body 的 Content-Length 一致）。gzip 流不支持分段解
    码——``.gz`` 对象的 Range 请求被忽略（始终全量返回）。
    """
    store = object_store
    row = None
    if manifest_first and store is not None and store.enabled:
        row = store.lookup(job_id, artifact_name)
        if row is not None:
            return open_raw_row(store, row, artifact_name, range_header, job=job)
    if artifact_path.exists() and artifact_path.is_file():
        return RawArtifact(name=artifact_name, path=artifact_path)
    if store is not None and store.enabled:
        if row is None:
            row = store.lookup(job_id, artifact_name)
        if row is not None:
            return open_raw_row(store, row, artifact_name, range_header, job=job)
    raise NotFoundError("Artifact not found")
