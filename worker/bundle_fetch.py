"""Worker-side bundle materialization (materials-and-runs design §6.2, #156).

A bundle-input job reaches the Worker as a ``runtime_context.material``
descriptor with ``kind: "bundle"`` and one presigned GET per member. This
module materializes every member into the Worker's content-addressed cache
(same mechanics as single materials, per-file dedup intact) and assembles
the folder's directory tree (``shared/material_bundle.py``), returning the
``runtime["materials"]`` block for the child payload. The bundle address
derives from the same manifest rule the Host uses, so both sides assemble
identical cache locations.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import Any

from shared.material_bundle import assemble_bundle_tree, bundle_address
from shared.material_cache import (
    MATERIALS_CACHE_DIRNAME,
    MaterializeError,
    cache_file_path,
    materialize_stream,
)
from worker.material_fetch import _open_download


def materialize_claim_bundle(material: Mapping[str, Any], execution_dir: Path) -> dict[str, Any]:
    """Materialize a ``kind: "bundle"`` claim descriptor into a dir tree."""
    bundle_id = str(material.get("material_id") or "").strip()
    entries = material.get("entries")
    if not bundle_id or not isinstance(entries, list) or not entries:
        raise MaterializeError("claim bundle descriptor is incomplete")
    cache_root = Path(execution_dir).parent / MATERIALS_CACHE_DIRNAME
    manifest: list[tuple[str, str]] = []
    parsed: list[tuple[str, str, str, str, int | None]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise MaterializeError("claim bundle entry is not an object")
        member_id = str(entry.get("material_id") or "").strip()
        url = str(entry.get("download_url") or "").strip()
        relpath = str(entry.get("path") or "").strip()
        if not member_id or not url or not relpath:
            raise MaterializeError("claim bundle entry is incomplete")
        content_hash = str(entry.get("content_hash") or "")
        size = entry.get("size_bytes")
        expected_size = int(size) if isinstance(size, (int, float)) else None
        address = content_hash or member_id
        parsed.append((address, url, relpath, content_hash, expected_size))
        manifest.append((address, relpath))
    tree_address = bundle_address(manifest)
    # 全程 pin 住全部成员与目录树：成员 i+1 的淘汰回合不得删掉成员 i
    # （与 Host 侧 bundle_runtime_block 同一纪律）。
    pin = {cache_file_path(cache_root, member_address) for member_address, _ in manifest}
    pin.add(cache_file_path(cache_root, tree_address))
    materialized: list[tuple[Path, str]] = []
    for address, url, relpath, content_hash, expected_size in parsed:
        path = materialize_stream(
            cache_root,
            address,
            partial(_open_download, url),
            expected_sha256=content_hash,
            expected_size=expected_size,
            pin=pin,
        )
        materialized.append((path, relpath))
    tree = assemble_bundle_tree(cache_root, tree_address, materialized)
    return {
        "material_id": bundle_id,
        "kind": "bundle",
        "path": str(tree),
        "filename": str(material.get("filename") or ""),
        "content_type": "",
        "size_bytes": int(material.get("size_bytes") or 0),
        "content_hash": tree_address,
        "entries": [
            {
                "path": str(entry.get("path") or ""),
                "size_bytes": int(entry.get("size_bytes") or 0),
                "content_hash": str(entry.get("content_hash") or ""),
            }
            for entry in entries
        ],
    }
