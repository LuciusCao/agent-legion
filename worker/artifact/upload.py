"""Direct-to-S3 artifact upload for the Worker (#160 D12).

When the claim manifest carries ``artifact_uploads`` (name → presigned PUT
URL), produced artifacts stream straight to object storage instead of the
legacy per-file ``POST /api/artifacts`` + result.tar.gz embed. The result
metadata then reports the object-storage ref form
``{"storage_key", "size_bytes", "content_hash"}``; the Host HEAD-verifies
every ref before applying it (server/app/agent_control/completion.py).

The URL arrives over the authenticated claim channel, so no SSRF guard
applies (same rule as the material download in ``worker.material_fetch``).
Retry semantics mirror ``worker.host.transfer``: transient network errors
and 5xx get exponential backoff, 4xx is a terminal verdict.

#338: a ``.gz``-suffixed spec asks for gzip-compressed PUT bytes (helpers in
``worker.artifact.gzip``); the reported ``content_hash`` stays the sha256 of
the uncompressed bytes.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, BinaryIO

import requests

from worker._retry import run_with_retry
from worker.artifact.download import describe_transfer_error
from worker.artifact.gzip import open_payload, prepare_upload
from worker.artifact.inputs import sha256_file

_PUT_TIMEOUT_SECONDS = 120
_RETRY_BASE_SECONDS = 1.0
_RETRY_MAX_ATTEMPTS = 3
_TRANSIENT_ERRORS = (requests.RequestException, TimeoutError, ConnectionError)


class DirectUploadError(RuntimeError):
    """Terminal (4xx) or exhausted direct-upload failure; the run reports failed."""


class _TransientUploadError(RuntimeError):
    """Internal carrier for one retried attempt's failure."""


def _put_stream(url: str, stream: BinaryIO, size_bytes: int) -> int:
    """PUT one open stream to the presigned URL; returns the status code.

    Module-level seam: tests monkeypatch this instead of touching the
    network. The stream is re-opened by the caller on every retry.
    """
    response = requests.put(url, data=stream, timeout=_PUT_TIMEOUT_SECONDS)
    response.close()
    return response.status_code


def upload_artifact_direct(
    path: Path, spec: Mapping[str, Any], *, stop: threading.Event | None = None
) -> dict[str, Any] | None:
    """Stream one artifact to its presigned PUT URL; returns the result ref.

    ``spec`` is the manifest's ``artifact_uploads[name]`` block
    (``storage_key`` + ``url``). The returned dict is the new-form
    ``output_artifacts`` value. None = stopped mid-retry (the pending marker
    stays and the next startup falls back to the legacy channel, since the
    presigned spec is never persisted). A ``.gz`` spec (#338) PUTs compressed
    bytes and reports the compressed ``size_bytes``.
    """
    if not isinstance(spec, Mapping):
        # Host bug / 协议错配：spec.get 会抛 AttributeError 逃逸出调用方的
        # DirectUploadError 捕获；归一为 DirectUploadError 走 CAS 回落。
        raise DirectUploadError(
            f"artifact upload spec has unexpected type {type(spec).__name__} for {path.name!r}"
        )
    url = str(spec.get("url") or "")
    storage_key = str(spec.get("storage_key") or "")
    if not url or not storage_key:
        raise DirectUploadError(f"artifact upload spec is incomplete for {path.name!r}")
    content_hash = sha256_file(path)
    payload, size_bytes = prepare_upload(path, storage_key)

    def attempt() -> bool:
        try:
            with open_payload(path, payload) as stream:
                status = _put_stream(url, stream, size_bytes)
        except _TRANSIENT_ERRORS as exc:
            # str(exc) 含完整签名 URL；只保留类型名，防止经 error_message 落库泄漏。
            raise _TransientUploadError(describe_transfer_error(exc)) from exc
        if status >= 500:
            raise _TransientUploadError(f"HTTP {status}")
        if status >= 400:
            raise DirectUploadError(f"artifact upload failed: HTTP {status}")
        if status not in (200, 201, 204):
            raise _TransientUploadError(f"HTTP {status}")
        return True

    try:
        result = run_with_retry(
            attempt,
            retriable=(_TransientUploadError,),
            terminal=(DirectUploadError,),
            base_seconds=_RETRY_BASE_SECONDS,
            max_attempts=_RETRY_MAX_ATTEMPTS,
            stop=stop,
        )
    except _TransientUploadError as exc:
        raise DirectUploadError(f"artifact upload failed: {exc}") from exc
    if result is None and stop is not None and stop.is_set():
        return None
    return {
        "storage_key": storage_key,
        "size_bytes": size_bytes,
        "content_hash": content_hash,
    }
