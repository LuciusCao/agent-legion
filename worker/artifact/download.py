"""Worker-side presigned-GET download for input artifacts (#160 D12).

Split out of ``worker.bundle_io`` for the file-size budget: that module owns
the legacy Host CAS channel, this one owns the object-storage channel. The
URL comes from the authenticated claim channel, so no SSRF guard applies
(same rule as ``worker.material_fetch``).
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

import requests

# Single socket timeout for presigned GET downloads; aligned with the
# transfer-timeout default of the bundle/artifact channel.
_DOWNLOAD_TIMEOUT_SECONDS = 120


def describe_transfer_error(exc: BaseException) -> str:
    """``str()`` of a requests/network exception embeds the full presigned URL
    (signature included); keep only the type name for persisted error
    messages. Shared by the artifact upload/download channels (it lives here
    because ``artifact_upload`` importing it would close an import cycle via
    ``bundle_io``)."""
    return type(exc).__name__


def _open_download(url: str) -> BinaryIO:
    """Open a streaming reader for a presigned GET URL.

    Module-level seam: tests monkeypatch this instead of touching the
    network.
    """
    try:
        response = requests.get(url, stream=True, timeout=_DOWNLOAD_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        # str(exc) 含完整签名 URL；只保留类型名，防止经 error_message 落库泄漏。
        raise RuntimeError(f"artifact download failed: {describe_transfer_error(exc)}") from exc
    if response.status_code != 200:
        response.close()
        raise RuntimeError(f"artifact download failed with HTTP {response.status_code}")
    return response.raw


def download_object_artifact(url: str, target: Path) -> None:
    """Stream a presigned GET to an atomic temp+rename (same .part hygiene
    as the Host-channel download in worker.host.client)."""
    if not url:
        raise RuntimeError("input artifact is missing its download URL")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    try:
        with _open_download(url) as stream, temporary.open("wb") as handle:
            while chunk := stream.read(1 << 20):
                handle.write(chunk)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
