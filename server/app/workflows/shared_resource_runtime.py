"""Host-side exact-pin shared-resource materialization (#1553 M43)."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import Any, BinaryIO

from server.app.storage import ObjectStorage
from server.app.workflows.shared_resources import (
    resource_freeze_from_run,
    resource_ids_for_node,
)
from shared.material_bundle import assemble_bundle_tree, bundle_address
from shared.material_cache import MaterializeError, cache_file_path, materialize_stream


def _verify_file(path: Path, file: Mapping[str, Any]) -> None:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise MaterializeError(f"shared resource file is unavailable: {file['path']}") from exc
    if size != int(file["size_bytes"]) or digest.hexdigest() != str(file["sha256"]):
        raise MaterializeError(f"shared resource file hash/size mismatch: {file['path']}")


def _storage(storage: ObjectStorage | None) -> ObjectStorage:
    if storage is None:
        raise MaterializeError("shared resource storage is not configured on this instance")
    return storage


def _open(storage: ObjectStorage, file: Mapping[str, Any]) -> BinaryIO:
    return storage.open_stream(str(file["storage_key"]))


def materialize_resource_pins(
    pins: list[Mapping[str, Any]],
    cache_root: Path,
    storage: ObjectStorage | None,
) -> dict[str, dict[str, Any]]:
    """Materialize only explicit job pins; no selection/latest lookup exists here."""
    if not pins:
        return {}
    object_storage = _storage(storage)
    prepared: list[tuple[Mapping[str, Any], list[dict[str, Any]], str]] = []
    pinned: set[Path] = set()
    for pin in pins:
        files = list(pin["files"])
        manifest = [(str(file["sha256"]), str(file["path"])) for file in files]
        address = bundle_address(manifest)
        prepared.append((pin, files, address))
        pinned.update(cache_file_path(cache_root, sha256) for sha256, _ in manifest)
        pinned.add(cache_file_path(cache_root, address))

    result: dict[str, dict[str, Any]] = {}
    for pin, files, address in prepared:
        materialized: list[tuple[Path, str]] = []
        for file in files:
            try:
                path = materialize_stream(
                    cache_root,
                    str(file["sha256"]),
                    partial(_open, object_storage, file),
                    expected_sha256=str(file["sha256"]),
                    expected_size=int(file["size_bytes"]),
                    pin=pinned,
                )
            except MaterializeError:
                raise
            except Exception as exc:
                # #204 broad-except audit: normalize the mixed object-store/cache failure
                # surface with resource context; the original exception stays chained.
                raise MaterializeError(
                    f"failed to materialize shared resource file {file['path']}: {exc}"
                ) from exc
            _verify_file(path, file)
            materialized.append((path, str(file["path"])))
        tree = assemble_bundle_tree(cache_root, address, materialized)
        public_files: list[dict[str, Any]] = []
        for file in files:
            local_path = tree / str(file["path"])
            _verify_file(local_path, file)
            public_files.append(
                {
                    "path": str(file["path"]),
                    "local_path": str(local_path),
                    "sha256": str(file["sha256"]),
                    "size_bytes": int(file["size_bytes"]),
                    "content_type": str(file["content_type"]),
                }
            )
        result[str(pin["resource_id"])] = {
            "resource_id": str(pin["resource_id"]),
            "version": int(pin["version"]),
            "manifest_hash": str(pin["manifest_hash"]),
            "path": str(tree),
            "files": public_files,
        }
    return result


def prefetch_shared_resource_block(
    executor: Any, context: Any, run: Mapping[str, Any] | None
) -> dict[str, dict[str, Any]]:
    job_db = executor.job_db
    read_pins = getattr(job_db, "get_job_shared_resource_pins", None)
    if not callable(read_pins):
        return {}
    pins = read_pins(str(context.job["id"]))
    freeze = resource_freeze_from_run(run)
    nodes = freeze.get("nodes")
    if isinstance(nodes, Mapping) and nodes:
        allowed = resource_ids_for_node(freeze, str(context.node_key))
        pins = [pin for pin in pins if str(pin["resource_id"]) in allowed]
    return materialize_resource_pins(
        pins,
        executor._materials_cache_root,
        executor._object_store(),
    )
