"""Object-layer gzip helpers for the Worker artifact channel (#338).

The claim spec's ``storage_key`` suffix is the compression marker — no
separate flag crosses the wire: a ``.gz`` upload spec asks for gzip-compressed
PUT bytes; a ``content_encoding: "gzip"`` input ref means the downloaded
object must be gunzipped. Hashes always describe the uncompressed bytes.
Mirrors ``server/app/services/job_artifact_gzip.py`` (the worker image ships
only worker/ + shared/; bump both together).
"""

from __future__ import annotations

import contextlib
import gzip
import io
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

GZIP_SUFFIX = ".gz"


def is_gzip_key(storage_key: str) -> bool:
    return storage_key.endswith(GZIP_SUFFIX)


def prepare_upload(path: Path, storage_key: str) -> tuple[bytes | None, int]:
    """Compress when the spec key carries the marker; return (payload, size).

    The payload is None for the raw form (the caller streams the file). The
    size is the stored byte count — for the gzip form the compressed length,
    the only number the Host can HEAD-verify.
    """
    if not is_gzip_key(storage_key):
        return None, path.stat().st_size
    # 产物上限 max_archive_bytes（默认 64 MiB），实际几十 KB JSON，内存内压缩。
    payload = gzip.compress(path.read_bytes())
    return payload, len(payload)


@contextlib.contextmanager
def open_payload(path: Path, payload: bytes | None) -> Iterator[BinaryIO]:
    """Open the PUT payload stream: in-memory compressed bytes or the file."""
    with io.BytesIO(payload) if payload is not None else path.open("rb") as stream:
        yield stream


def copy_stream(source: BinaryIO, handle: BinaryIO, *, gunzip: bool = False) -> None:
    """Copy a download stream into an open handle, gunzipping when marked.

    ``gzip.GzipFile.close()`` leaves a caller-supplied fileobj open, and the
    caller owns the raw stream's close, so only the decoder is closed here.
    """
    decoded = gzip.GzipFile(fileobj=source) if gunzip else None
    try:
        reader = decoded if decoded is not None else source
        while chunk := reader.read(1 << 20):
            handle.write(chunk)
    finally:
        if decoded is not None:
            decoded.close()
