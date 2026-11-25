"""Worker materialization of exact-pinned shared resources."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, Sequence
from functools import partial
from pathlib import Path
from typing import Any

from shared.material_bundle import assemble_bundle_tree, bundle_address
from shared.material_cache import (
    MATERIALS_CACHE_DIRNAME,
    MaterializeError,
    cache_file_path,
    material_cache_address,
    materialize_stream,
)
from shared.resource_manifest import (
    SHARED_RESOURCE_CLAIM_FORMAT,
    claim_resource_ids,
    validated_pin_files,
)
from worker.material_fetch import _open_download


def _job_snapshot(manifest: Mapping[str, Any]) -> Any:
    context = manifest.get("runtime_context")
    job = context.get("job") if isinstance(context, Mapping) else None
    return job.get("workflow_definition_snapshot_json") if isinstance(job, Mapping) else None


def child_resource_ids(
    manifest: Mapping[str, Any], shared_resources: Mapping[str, Any] | None
) -> tuple[str, ...]:
    resource_ids = claim_resource_ids(manifest, _job_snapshot(manifest))
    if resource_ids and (
        not isinstance(shared_resources, Mapping) or set(shared_resources) != set(resource_ids)
    ):
        raise ValueError("shared resources are missing or do not match the node declaration")
    if not resource_ids and shared_resources:
        raise ValueError("shared resources were materialized without a node declaration")
    return resource_ids


def _verify_file(path: Path, file: Mapping[str, Any]) -> None:
    digest = hashlib.sha256()
    size = 0
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("not a regular file")
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise MaterializeError(f"shared resource file is unavailable: {file['path']}") from exc
    if size != file["size_bytes"] or digest.hexdigest() != file["sha256"]:
        raise MaterializeError(f"shared resource file hash/size mismatch: {file['path']}")


def _verify_tree(tree: Path, files: Sequence[Mapping[str, Any]]) -> None:
    if tree.is_symlink() or not tree.is_dir():
        raise MaterializeError("shared resource directory is unavailable")
    actual: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(tree, followlinks=False):
        for name in dirnames + filenames:
            if (Path(dirpath) / name).is_symlink():
                raise MaterializeError("shared resource directory contains a symlink")
        for name in filenames:
            actual.add((Path(dirpath) / name).relative_to(tree).as_posix())
    expected = {str(file["path"]) for file in files}
    if actual != expected:
        raise MaterializeError("shared resource directory does not match its manifest")
    for file in files:
        _verify_file(tree / str(file["path"]), file)


def _ordinary_material_pins(
    manifest: Mapping[str, Any], materials: Mapping[str, Any] | None, cache_root: Path
) -> set[Path]:
    pinned: set[Path] = set()
    if materials and materials.get("path"):
        pinned.add(Path(str(materials["path"])))
    context = manifest.get("runtime_context")
    material = context.get("material") if isinstance(context, Mapping) else None
    if not isinstance(material, Mapping):
        return pinned
    entries = material.get("entries") if material.get("kind") == "bundle" else [material]
    for entry in entries or []:
        if not isinstance(entry, Mapping):
            continue
        member_id = str(entry.get("material_id") or "")
        if not member_id:
            continue
        generation = entry.get("upload_generation")
        generation = int(generation) if isinstance(generation, (int, float)) else None
        address = material_cache_address(
            member_id, str(entry.get("content_hash") or ""), generation
        )
        pinned.add(cache_file_path(cache_root, address))
    return pinned


def _parse_descriptors(
    manifest: Mapping[str, Any], resource_ids: tuple[str, ...]
) -> list[tuple[Mapping[str, Any], list[dict[str, Any]], str]]:
    context = manifest.get("runtime_context")
    descriptor = context.get("shared_resources") if isinstance(context, Mapping) else None
    if (
        not isinstance(descriptor, Mapping)
        or descriptor.get("format") != SHARED_RESOURCE_CLAIM_FORMAT
    ):
        raise MaterializeError("shared resource claim descriptor is missing or unsupported")
    pins = descriptor.get("pins")
    if not isinstance(pins, list):
        raise MaterializeError("shared resource claim descriptor has no pin list")
    by_id: dict[str, Mapping[str, Any]] = {}
    for pin in pins:
        if not isinstance(pin, Mapping):
            raise MaterializeError("shared resource claim pin is not an object")
        resource_id = pin.get("resource_id")
        if not isinstance(resource_id, str) or not resource_id or resource_id in by_id:
            raise MaterializeError("shared resource claim pin has an invalid resource id")
        by_id[resource_id] = pin
    if set(by_id) != set(resource_ids):
        raise MaterializeError("shared resource claim pins do not match the node declaration")

    parsed: list[tuple[Mapping[str, Any], list[dict[str, Any]], str]] = []
    for resource_id in resource_ids:
        pin = by_id[resource_id]
        try:
            public_files = validated_pin_files(pin)
            version = pin["version"]
            if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
                raise ValueError("invalid version")
            originals = {str(file["path"]): file for file in pin["files"]}
            files: list[dict[str, Any]] = []
            for file in public_files:
                original = originals[file["path"]]
                url = original.get("download_url")
                if not isinstance(url, str) or not url:
                    raise ValueError("missing download URL")
                files.append({**file, "download_url": url})
        except (KeyError, TypeError, ValueError) as exc:
            raise MaterializeError(
                f"shared resource descriptor failed verification: {resource_id}"
            ) from exc
        address = bundle_address((file["sha256"], file["path"]) for file in files)
        parsed.append((pin, files, address))
    return parsed


def materialize_claim_shared_resources(
    manifest: Mapping[str, Any],
    execution_dir: Path,
    *,
    materials: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    resource_ids = claim_resource_ids(manifest, _job_snapshot(manifest))
    context = manifest.get("runtime_context")
    descriptor = context.get("shared_resources") if isinstance(context, Mapping) else None
    if not resource_ids:
        if descriptor:
            raise MaterializeError("shared resource descriptor has no node declaration")
        return {}
    parsed = _parse_descriptors(manifest, resource_ids)
    cache_root = Path(execution_dir).parent / MATERIALS_CACHE_DIRNAME
    pinned = _ordinary_material_pins(manifest, materials, cache_root)
    for _pin, files, address in parsed:
        pinned.update(cache_file_path(cache_root, file["sha256"]) for file in files)
        pinned.add(cache_file_path(cache_root, address))

    result: dict[str, dict[str, Any]] = {}
    for pin, files, address in parsed:
        resource_id = str(pin["resource_id"])
        materialized: list[tuple[Path, str]] = []
        for file in files:
            try:
                path = materialize_stream(
                    cache_root,
                    file["sha256"],
                    partial(_open_download, file["download_url"]),
                    expected_sha256=file["sha256"],
                    expected_size=file["size_bytes"],
                    pin=pinned,
                )
                _verify_file(path, file)
            except MaterializeError:
                raise
            except OSError as exc:
                raise MaterializeError(
                    f"failed to materialize shared resource file {file['path']}"
                ) from exc
            materialized.append((path, file["path"]))
        try:
            tree = assemble_bundle_tree(cache_root, address, materialized)
        except OSError as exc:
            raise MaterializeError(
                f"failed to assemble shared resource directory: {resource_id}"
            ) from exc
        _verify_tree(tree, files)
        result[resource_id] = {
            "resource_id": resource_id,
            "version": pin["version"],
            "manifest_hash": pin["manifest_hash"],
            "path": str(tree),
            "files": [
                {
                    "path": file["path"],
                    "local_path": str(tree / file["path"]),
                    "sha256": file["sha256"],
                    "size_bytes": file["size_bytes"],
                    "content_type": file["content_type"],
                }
                for file in files
            ],
        }
    return result
