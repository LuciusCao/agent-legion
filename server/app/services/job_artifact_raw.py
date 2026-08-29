"""Raw (binary) artifact location: local job_dir file or object-store stream.

Split from services/job_artifacts.py for the architecture file budget; the
text read path stays in JobArtifactService.
"""

from __future__ import annotations

import logging
from pathlib import Path

from server.app.http_range import parse_range_header
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_artifact_raw_types import RawArtifact

__all__ = ["RawArtifact", "open_raw_artifact"]
from server.app.services.job_errors import NotFoundError

logger = logging.getLogger(__name__)


def open_raw_artifact(
    artifact_path: Path,
    object_store: JobArtifactObjectStore | None,
    job_id: str,
    artifact_name: str,
    range_header: str | None = None,
) -> RawArtifact:
    """Locate a binary-servable artifact: local job_dir file first, then the
    object-store stream (mirrors the text read()'s ordering).

    range_header 解析为单区间后走对象存储的 ranged read（本地分支忽略
    区间——FileResponse 自行处理 Range 头）。存储故障按 NotFoundError
    降级，与 read() 一致；消费（与关闭）返回句柄是调用方责任。
    """
    if artifact_path.exists() and artifact_path.is_file():
        return RawArtifact(name=artifact_name, path=artifact_path)
    store = object_store
    if store is not None and store.enabled:
        row = store.lookup(job_id, artifact_name)
        if row is not None:
            size = row.get("size_bytes")
            size_bytes = int(size) if isinstance(size, int) else None
            byte_range = parse_range_header(range_header, size_bytes)
            try:
                if byte_range is not None:
                    stream = store.open_range_stream(row, *byte_range)
                else:
                    stream = store.open_stream(row)
            except Exception:
                logger.warning(
                    "failed to open raw artifact %s of job %s from object storage",
                    artifact_name,
                    job_id,
                    exc_info=True,
                )
                raise NotFoundError("Artifact not found") from None
            return RawArtifact(
                name=artifact_name,
                stream=stream,
                size_bytes=size_bytes,
                range_start=byte_range[0] if byte_range else None,
                range_end=byte_range[1] if byte_range else None,
            )
    raise NotFoundError("Artifact not found")
