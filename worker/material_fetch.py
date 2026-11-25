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

from shared.material_cache import (
    MATERIALS_CACHE_DIRNAME,
    MaterializeError,
    material_cache_address,
)
from worker.presigned_download import (
    materialize_presigned_stream,
    open_presigned_download,
)


def _open_download(url: str, *, if_match: str = "") -> BinaryIO:
    """Compatibility seam for material, bundle and shared-resource callers."""
    return open_presigned_download(url, if_match=if_match)


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
    object_etag = str(material.get("object_etag") or "")
    # 无 hash 行的缓存地址带代次（#1279 codex r6：代次即身份——repair 代次
    # +1 后旧地址自然孤儿化，修复前（可能被旧 Worker 无核验缓存的）毒化
    # 字节不再命中）。旧 Server 的描述符无 upload_generation → None 回落
    # 裸 id（既有形态）。代次与 download_url 来自同一行快照，恒一致。
    generation = material.get("upload_generation")
    generation = int(generation) if isinstance(generation, (int, float)) else None
    size = material.get("size_bytes")
    expected_size = int(size) if isinstance(size, (int, float)) else None
    cache_root = Path(execution_dir).parent / MATERIALS_CACHE_DIRNAME
    path = materialize_presigned_stream(
        cache_root,
        material_cache_address(material_id, content_hash, generation),
        lambda: _open_download(url, if_match=object_etag),
        label=f"material {material_id}",
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
