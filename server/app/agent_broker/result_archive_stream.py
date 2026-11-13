"""Python-version-compatible, bounded gzip streams for result archive scans."""

from __future__ import annotations

import inspect
import tarfile
from pathlib import Path
from typing import Any


class _ScanLimitedDecompressor:
    def __init__(self, decompressor: Any, remaining: int) -> None:
        self._decompressor = decompressor
        self._remaining, self._pending = remaining, b""

    def decompress(self, data: bytes) -> bytes:
        compressed = self._pending + data
        payload: bytes = self._decompressor.decompress(compressed, self._remaining + 1)
        self._pending = self._decompressor.unconsumed_tail
        self._remaining -= len(payload)
        if self._remaining < 0:
            raise ValueError("result archive scan exceeds decompressed-byte limit")
        return payload


def open_result_archive_stream(archive: Path, max_scan_bytes: int) -> Any:
    stream_factory: Any = tarfile._Stream  # type: ignore[attr-defined]
    options: dict[str, int] = {}
    if "compresslevel" in inspect.signature(stream_factory).parameters:
        options["compresslevel"] = 9
    stream = stream_factory(str(archive), "r", "gz", None, tarfile.RECORDSIZE, **options)
    stream.cmp = _ScanLimitedDecompressor(stream.cmp, max_scan_bytes)
    return stream
