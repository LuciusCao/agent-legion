"""HTTP Range 头解析（raw 端点的对象存储分支）。

单区间 "bytes=start-end"（闭区间）是媒体 seek 的全部需求；多区间 /
后缀区间（bytes=-N）忽略并回退全量——正确性优先于吞吐。

``parse_range_header_strict``（#1259）：.gz 对象的 Range 按解压口径应
答时需要区分「良构但不可满足」（416）与「忽略 Range」（全量应答）——
宽松版把两者都归一为 None，无法支撑 416。
"""

from __future__ import annotations

import re

_RANGE_HEADER_RE = re.compile(r"^bytes=(\d+)-(\d*)$")


class UnsatisfiableRange:
    """416 哨兵：良构但不可满足的单区间（区别于「忽略 Range」的 None）。"""


UNSATISFIABLE_RANGE = UnsatisfiableRange()


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


def parse_range_header_strict(
    range_header: str | None, size_bytes: int | None
) -> tuple[int, int] | UnsatisfiableRange | None:
    """严格变体（#1259）：良构但不可满足（起点越界或 end < start）返回
    UNSATISFIABLE_RANGE；缺头/未知长度/畸形/多区间/后缀区间返回 None。"""
    if not range_header or size_bytes is None:
        return None
    match = _RANGE_HEADER_RE.match(range_header.strip())
    if match is None:
        return None
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else size_bytes - 1
    if start >= size_bytes or end < start:
        return UNSATISFIABLE_RANGE
    return (start, min(end, size_bytes - 1))
