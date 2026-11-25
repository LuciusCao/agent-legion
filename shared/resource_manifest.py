"""Shared-resource declaration and manifest checks used by Host and Worker."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any

SHARED_RESOURCE_CLAIM_FORMAT = "shared-resource-claim-v1"
_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")


def resource_manifest_hash(files: Iterable[Mapping[str, Any]]) -> str:
    canonical = [
        {
            "path": str(file["path"]),
            "sha256": str(file["sha256"]),
            "size_bytes": int(file["size_bytes"]),
            "content_type": str(file["content_type"]),
        }
        for file in sorted(files, key=lambda item: str(item["path"]))
    ]
    encoded = json.dumps(
        {"format": "shared-resource-manifest-v1", "files": canonical},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def snapshot_resource_ids(snapshot: Any, node_key: str) -> tuple[str, ...]:
    if not snapshot:
        return ()
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except json.JSONDecodeError as exc:
            raise ValueError("shared resource declaration snapshot is invalid") from exc
    if not isinstance(snapshot, Mapping):
        raise ValueError("shared resource declaration snapshot is invalid")
    nodes = snapshot.get("nodes")
    node = nodes.get(node_key) if isinstance(nodes, Mapping) else None
    if node is None:
        return ()
    if not isinstance(node, Mapping):
        raise ValueError("shared resource declaration node is invalid")
    return normalize_resource_ids(node.get("shared_resources"), "workflow snapshot")


def normalize_resource_ids(value: Any, source: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple) or any(
        not isinstance(resource_id, str) or not resource_id for resource_id in value
    ):
        raise ValueError(f"{source} shared resource declaration must be a list of stable ids")
    if len(set(value)) != len(value):
        raise ValueError(f"{source} shared resource declaration contains duplicate ids")
    return tuple(value)


def claim_resource_ids(manifest: Mapping[str, Any], job_snapshot: Any) -> tuple[str, ...]:
    node_key = str(manifest.get("node_key") or "")
    snapshot_ids = snapshot_resource_ids(job_snapshot, node_key)
    manifest_ids = normalize_resource_ids(manifest.get("shared_resources"), "code manifest")
    if snapshot_ids and manifest_ids and set(snapshot_ids) != set(manifest_ids):
        raise ValueError("shared resource declaration differs from the job snapshot")
    if job_snapshot and not snapshot_ids and manifest_ids:
        raise ValueError("shared resource declaration differs from the job snapshot")
    ids = snapshot_ids or manifest_ids
    context = manifest.get("runtime_context")
    descriptor = context.get("shared_resources") if isinstance(context, Mapping) else None
    if descriptor and not ids:
        raise ValueError("shared resource descriptor has no node declaration")
    return ids


def validate_resource_path(value: Any) -> str:
    if not isinstance(value, str) or not value or value.strip().strip("/") != value:
        raise ValueError("shared resource file path is not canonical")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or "\\" in value
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
        or any(segment in ("", ".", "..") for segment in value.split("/"))
    ):
        raise ValueError(f"shared resource file path is unsafe: {value!r}")
    return value


def validated_pin_files(pin: Mapping[str, Any]) -> list[dict[str, Any]]:
    files = pin.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("shared resource descriptor has no files")
    validated: list[dict[str, Any]] = []
    paths: set[str] = set()
    for file in files:
        if not isinstance(file, Mapping):
            raise ValueError("shared resource file descriptor is not an object")
        path = validate_resource_path(file.get("path"))
        if path in paths:
            raise ValueError("shared resource file paths must be unique")
        paths.add(path)
        sha256 = file.get("sha256")
        size_bytes = file.get("size_bytes")
        content_type = file.get("content_type")
        if not isinstance(sha256, str) or _SHA256_HEX_RE.fullmatch(sha256) is None:
            raise ValueError(f"shared resource file has an invalid SHA256: {path}")
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
            raise ValueError(f"shared resource file has an invalid size: {path}")
        if not isinstance(content_type, str):
            raise ValueError(f"shared resource file has an invalid content type: {path}")
        validated.append(
            {
                "path": path,
                "sha256": sha256,
                "size_bytes": size_bytes,
                "content_type": content_type,
            }
        )
    manifest_hash = pin.get("manifest_hash")
    if not isinstance(manifest_hash, str) or _SHA256_HEX_RE.fullmatch(manifest_hash) is None:
        raise ValueError("shared resource descriptor has an invalid manifest hash")
    if resource_manifest_hash(validated) != manifest_hash:
        raise ValueError("shared resource descriptor manifest hash does not match its files")
    return validated
