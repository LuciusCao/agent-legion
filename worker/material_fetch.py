"""Worker-side material materialization (materials-and-runs design §6.2).

A material-input job reaches the Worker as a ``runtime_context.material``
descriptor on the claim response (metadata + a claim-time presigned GET
URL — the Worker holds no object-storage credentials). This module
materializes the bytes into the Worker's local content-addressed cache
(``<work_root>/materials_cache``, shared mechanics in
``shared/material_cache.py``) and returns the ``runtime["materials"]``
block for the child payload. The sandboxed node then reads the local file
through the static allow-read grant (MATERIAL-ACCESS-001); the URL itself
never crosses into the sandbox.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, BinaryIO

import requests

from shared.material_cache import (
    MATERIALS_CACHE_DIRNAME,
    MaterializeError,
    materialize_stream,
)

# Single socket timeout for the material download; aligned with the
# transfer-timeout default of the bundle/artifact channel.
_DOWNLOAD_TIMEOUT_SECONDS = 120


def _open_download(url: str) -> BinaryIO:
    """Open a streaming reader for the presigned GET URL.

    Module-level seam: tests monkeypatch this instead of touching the
    network. The URL comes from the authenticated claim channel, so no
    SSRF guard applies here (unlike node-originated downloads).
    """
    response = requests.get(url, stream=True, timeout=_DOWNLOAD_TIMEOUT_SECONDS)
    if response.status_code != 200:
        response.close()
        raise MaterializeError(f"material download failed with HTTP {response.status_code}")
    return response.raw


def materialize_claim_material(
    manifest: Mapping[str, Any], execution_dir: Path
) -> dict[str, Any] | None:
    """Materialize the claim's material descriptor; None when not material."""
    context = manifest.get("runtime_context")
    material = context.get("material") if isinstance(context, Mapping) else None
    if not isinstance(material, Mapping):
        return None
    if material.get("kind") == "bundle":
        # Deferred import: bundle_fetch depends on this module (#156).
        from worker.bundle_fetch import materialize_claim_bundle

        return materialize_claim_bundle(material, execution_dir)
    material_id = str(material.get("material_id") or "").strip()
    url = str(material.get("download_url") or "").strip()
    if not material_id or not url:
        raise MaterializeError("claim material descriptor is incomplete")
    content_hash = str(material.get("content_hash") or "")
    filename = str(material.get("filename") or "")
    size = material.get("size_bytes")
    expected_size = int(size) if isinstance(size, (int, float)) else None
    cache_root = Path(execution_dir).parent / MATERIALS_CACHE_DIRNAME
    path = materialize_stream(
        cache_root,
        content_hash or material_id,
        lambda: _open_download(url),
        expected_sha256=content_hash,
        expected_size=expected_size,
    )
    return {
        "material_id": material_id,
        "path": str(path),
        "filename": filename,
        "content_type": str(material.get("content_type") or ""),
        "size_bytes": expected_size or 0,
        "content_hash": content_hash,
    }
