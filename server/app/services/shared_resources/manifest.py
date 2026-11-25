"""Canonical manifests and source-snapshot validation."""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import closing
from typing import Any, BinaryIO

from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.material_bundles import MAX_BUNDLE_MEMBERS, validate_member_path
from server.app.storage import ObjectStorage
from shared.resource_manifest import resource_manifest_hash

_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")


def _sha256_stream(stream: BinaryIO) -> tuple[int, str]:
    digest = hashlib.sha256()
    size_bytes = 0
    while chunk := stream.read(1024 * 1024):
        size_bytes += len(chunk)
        digest.update(chunk)
    return size_bytes, digest.hexdigest()


def _digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def request_fingerprint(
    *, workspace_id: str, source_type: str, source_id: str, name: str = "", resource_id: str = ""
) -> str:
    return _digest(
        {
            "workspace_id": workspace_id,
            "resource_id": resource_id,
            "name": name,
            "source_type": source_type,
            "source_id": source_id,
        }
    )


def manifest_hash(files: list[dict[str, Any]]) -> str:
    return resource_manifest_hash(files)


def prepare_source_snapshot(
    snapshot: dict[str, Any] | None, storage: ObjectStorage
) -> dict[str, Any]:
    if snapshot is None:
        raise NotFoundError("Shared resource source not found")
    files = snapshot.get("files") or []
    if not files:
        raise InvalidOperationError("Shared resource source requires at least one file")
    if len(files) > MAX_BUNDLE_MEMBERS:
        raise InvalidOperationError(
            f"Shared resource source exceeds the {MAX_BUNDLE_MEMBERS} file limit"
        )
    paths: set[str] = set()
    prepared = []
    for original in files:
        file = dict(original)
        if file["status"] != "ready":
            raise InvalidOperationError(
                f"Source material is not ready: {file['source_material_id']}"
            )
        if not file["source_etag"]:
            raise InvalidOperationError(
                f"Source material has no immutable object identity: {file['source_material_id']}"
            )
        file["path"] = validate_member_path(str(file["path"]))
        if file["path"] in paths:
            raise InvalidOperationError("Shared resource file paths must be unique")
        paths.add(file["path"])
        if int(file["size_bytes"]) < 0:
            raise InvalidOperationError("Source material size must be non-negative")
        declared = str(file["sha256"])
        if declared and _SHA256_HEX_RE.fullmatch(declared) is None:
            raise InvalidOperationError("Source material hash is not a SHA256 digest")
        if not declared:
            try:
                with closing(
                    storage.open_stream(
                        str(file["source_storage_key"]), if_match=str(file["source_etag"])
                    )
                ) as stream:
                    actual_size, actual_hash = _sha256_stream(stream)
            except Exception as exc:
                # #204 broad-except audit: storage adapters expose several exception
                # families; all become a source-verification failure without an intent
                # or visible resource. Programming errors are not used for control flow.
                raise InvalidOperationError("Source object could not be hash-verified") from exc
            if actual_size != int(file["size_bytes"]):
                raise InvalidOperationError("Source object size does not match its material row")
            file["sha256"] = actual_hash
        prepared.append(file)
    prepared.sort(key=lambda item: item["path"])
    return {**snapshot, "files": prepared}
