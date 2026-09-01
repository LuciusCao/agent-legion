"""Object-layer gzip form marker + form-aware content checks (#338).

A ``storage_key`` with the ``.gz`` suffix holds gzip-compressed bytes;
absence means raw bytes. The suffix is the only form marker — read paths
never guess from content. ``content_hash`` (and every sha256 digest check)
always describes the UNCOMPRESSED bytes, so content readers wrap stored
streams with ``GunzipStream`` and stay dual-form with zero data migration,
while size-based maintenance checks must treat the two forms differently
(``row_stale`` / ``size_certified``).

The Worker image ships only worker/ + shared/, so the Worker side mirrors
these constants in ``worker/artifact/gzip.py`` (bump both together — the
wire contract is the suffix itself).
"""

from __future__ import annotations

import gzip
import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO, cast

from server.app.storage import ObjectStorage

GZIP_SUFFIX = ".gz"


def is_gzip_key(storage_key: str) -> bool:
    """True when the storage key marks a gzip-compressed object (#338)."""
    return storage_key.endswith(GZIP_SUFFIX)


def content_stream(storage: ObjectStorage, storage_key: str) -> BinaryIO:
    """Open the content-byte stream for a stored object: ``.gz`` keys decode
    transparently, bare keys pass through."""
    stream = storage.open_stream(storage_key)
    if is_gzip_key(storage_key):
        return cast(BinaryIO, GunzipStream(stream))
    return stream


class GunzipStream:
    """Read-through gzip decoder that also owns the underlying stream's close.

    ``gzip.GzipFile.close()`` deliberately leaves a caller-supplied fileobj
    open; this wrapper closes both so ``with store.open_stream(row)`` never
    leaks the botocore StreamingBody connection.
    """

    def __init__(self, raw: BinaryIO) -> None:
        self._raw = raw
        self._decoded = gzip.GzipFile(fileobj=raw)

    def read(self, size: int = -1) -> bytes:
        return self._decoded.read(size)

    def close(self) -> None:
        try:
            self._decoded.close()
        finally:
            self._raw.close()

    def __enter__(self) -> GunzipStream:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def read_bounded(stream: BinaryIO, limit: int | None, *, name: str = "artifact") -> Iterator[bytes]:
    """Yield the stream's content bytes; raise once they exceed ``limit``.

    Decompression-bomb guard (#338): a ``.gz`` object's stored size passes
    HEAD/size verification, but the DECOMPRESSED bytes are what land in the
    staging dir / job_dir — count and cut off mid-stream instead of
    materializing a bomb to disk before the digest check.
    """
    total = 0
    while chunk := stream.read(1 << 20):
        total += len(chunk)
        if limit is not None and total > limit:
            raise ValueError(f"{name} decompresses beyond the size limit {limit}")
        yield chunk


def file_sha256(path: Path) -> str:
    """Streamed digest: artifacts can be multi-GB, never buffer them whole."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def row_stale(row: dict[str, Any], local_path: Path) -> bool:
    """True when the local file no longer matches the manifest row.

    A node re-run can produce new bytes while the best-effort upload fails;
    the old row must not suppress the re-upload forever (and must never
    certify the new local file for cache eviction). #338: a ``.gz`` row
    records the COMPRESSED size — incomparable to the local uncompressed
    file, so the content hash (always uncompressed) alone decides.
    """
    try:
        local_size = local_path.stat().st_size
    except OSError:
        return False
    if not is_gzip_key(str(row["storage_key"])) and int(row["size_bytes"]) != local_size:
        return True
    recorded = str(row.get("content_hash") or "")
    return bool(recorded) and recorded != file_sha256(local_path)


def size_certified(entries: list[tuple[int, str, bool]], local_size: int) -> bool:
    """Eviction size gate (#338): ``.gz``-flagged entries record compressed
    sizes and cannot confirm local bytes by size; when only ``.gz`` rows
    exist the content hash alone certifies the local copy."""
    sizes = {size for size, _hash, gz in entries if not gz}
    return not sizes or local_size in sizes
