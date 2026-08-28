"""RawArtifact 数据类：raw 端点的产物句柄。

拆自 services/job_artifact_raw.py 的架构文件预算拆分（模型与逻辑分居）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True)
class RawArtifact:
    """A binary artifact handle: local file path or an object-store stream.

    Local files are served by FileResponse (native Range support for media
    seeking); stream-backed artifacts come from object storage — ranged
    reads (media seek) carry the requested inclusive byte range.
    """

    name: str
    path: Path | None = None
    stream: BinaryIO | None = None
    size_bytes: int | None = None
    # 请求的字节区间（闭区间）；None = 全量。仅在对象存储分支生效。
    range_start: int | None = None
    range_end: int | None = None
