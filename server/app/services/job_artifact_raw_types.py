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

    ``content_encoding`` (#338): ``"gzip"`` marks a ``.gz`` object served as
    its stored (compressed) bytes — the route passes them through with a
    ``Content-Encoding: gzip`` response header (browsers decode natively,
    API clients gunzip themselves). #1259: this passthrough form is only for
    requests WITHOUT a Range header; ranged ``.gz`` requests are answered in
    decompressed units (see job_artifact_raw_gzip).

    ``unsatisfiable_range`` (#1259): the requested range lies beyond the
    representation length — the route answers 416 with ``Content-Range:
    bytes */<size_bytes>``; stream/path stay None.

    ``unsliced_stream`` (#1259): True means ``stream`` yields the FULL
    representation bytes (a gunzipped ``.gz`` stream — gzip decodes
    sequentially, so slicing happens at serve time) and the route slices
    [range_start, range_end] out of it; False (default) means the stream
    already carries only the ranged bytes (object-store ranged read).
    """

    name: str
    path: Path | None = None
    stream: BinaryIO | None = None
    size_bytes: int | None = None
    # 请求的字节区间（闭区间）；None = 全量。仅在对象存储分支生效。
    range_start: int | None = None
    range_end: int | None = None
    # 对象存储存储形态的 Content-Encoding 透传标记（#338）；None = 裸字节。
    content_encoding: str | None = None
    # 416 标记（#1259）：请求区间越过表示长度；stream/path 均 None。
    unsatisfiable_range: bool = False
    # True = stream 是全量表示字节（.gz 解压流），由响应层切片到区间。
    unsliced_stream: bool = False
