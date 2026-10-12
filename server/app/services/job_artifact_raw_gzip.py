""".gz 对象的 Range 请求应答（#1259）：解压口径的 206 / 416 / 全量 200。

#338 的 .gz 透传（``Content-Encoding: gzip`` + 存储字节，Range 被忽略、
永远 200 全量）对无 Range 请求维持不变；但「请求带 Range 却收到 200 +
gzip 编码」被浏览器网络栈判为协议失败（fetch 抛 TypeError、媒体元素
触发 error——对象存储 + .gz 形态下内置产物预览全挂）。带 Range 的 .gz
请求因此切换到解压口径：

- 良构可满足区间 → 解压流 + 区间标记（``unsliced_stream``——gzip 顺序
  解码无法按存储字节分段，切片由响应层在供给时执行），Content-Range
  分母是解压长度；
- 良构不可满足（起点越界 / end < start）→ 416 标记；
- 不支持形态（多区间 / 后缀区间）或解压长度未知 → 忽略 Range，全量解
  压 200（对 Range 回 200 全量合法——浏览器拒绝的只是 200+gzip 组合）。

解压长度取自 gzip trailer 的 ISIZE（最后 4 字节，LE）：Worker 上传是
``gzip.compress`` 的单成员流（worker/artifact/gzip.py），ISIZE 即真实
解压长度（模 2^32——产物上限 max_archive_bytes 远低于 4 GiB，实际精
确）。trailer 经 store 的 ranged read 读存储字节末 8 字节（存储字节平
面，与 open_object_stream 同层，不是内容区间语义）。
"""

from __future__ import annotations

from typing import Any

from server.app.http_range import UnsatisfiableRange, parse_range_header_strict
from server.app.services.job_artifact_objects import JobArtifactObjectStore
from server.app.services.job_artifact_raw_types import RawArtifact

__all__ = ["open_gzip_ranged"]

# gzip 成员最小长度：10 字节头 + 8 字节 trailer（CRC32 + ISIZE）。
_MIN_GZIP_MEMBER_BYTES = 18
_TRAILER_BYTES = 8


def open_gzip_ranged(
    store: JobArtifactObjectStore,
    row: dict[str, Any],
    artifact_name: str,
    range_header: str,
    size_bytes: int | None,
) -> RawArtifact:
    """带 Range 的 .gz 对象句柄（语义见模块 docstring）。

    存储故障（ClientError/BotoCoreError）不在此捕获——调用方
    （open_raw_row）的既有 except 统一降级为 NotFoundError。
    """
    decoded_size = _decoded_size(store, row, size_bytes)
    parsed = parse_range_header_strict(range_header, decoded_size)
    if isinstance(parsed, UnsatisfiableRange):
        return RawArtifact(name=artifact_name, size_bytes=decoded_size, unsatisfiable_range=True)
    # open_stream 的 #338 双形态契约：.gz 对象透明解压（GunzipStream）。
    stream = store.open_stream(row)
    if parsed is None:
        return RawArtifact(name=artifact_name, stream=stream, size_bytes=decoded_size)
    start, end = parsed
    return RawArtifact(
        name=artifact_name,
        stream=stream,
        size_bytes=decoded_size,
        range_start=start,
        range_end=end,
        unsliced_stream=True,
    )


def _decoded_size(
    store: JobArtifactObjectStore, row: dict[str, Any], size_bytes: int | None
) -> int | None:
    """gzip trailer ISIZE 的解压长度；不可定位（size 缺失/过短/短读）时 None。"""
    if size_bytes is None or size_bytes < _MIN_GZIP_MEMBER_BYTES:
        return None
    stream = store.open_range_stream(row, size_bytes - _TRAILER_BYTES, size_bytes - 1)
    try:
        trailer = stream.read(_TRAILER_BYTES)
    finally:
        stream.close()
    if len(trailer) != _TRAILER_BYTES:
        return None
    return int.from_bytes(trailer[4:], "little")
