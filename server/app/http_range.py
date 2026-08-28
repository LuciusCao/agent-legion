"""HTTP Range 头解析（raw 端点的对象存储分支）。

单区间 "bytes=start-end"（闭区间）是媒体 seek 的全部需求；多区间 /
后缀区间（bytes=-N）忽略并回退全量——正确性优先于吞吐。
"""

from __future__ import annotations

import re

_RANGE_HEADER_RE = re.compile(r"^bytes=(\d+)-(\d*)$")


def parse_range_header(range_header: str | None, size_bytes: int | None) -> tuple[int, int] | None:
    """解析单区间（闭区间），越界裁剪；返回 None = 全量。"""
    if not range_header or size_bytes is None or size_bytes <= 0:
        return None
    match = _RANGE_HEADER_RE.match(range_header.strip())
    if match is None:
        return None
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else size_bytes - 1
    if start >= size_bytes:
        return None
    return (start, min(end, size_bytes - 1))
