"""Claim-time Worker descriptors rebuilt from exact shared-resource job pins."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.db.dialect import ConnectSource
from server.app.db.transaction import read_connection
from server.app.jobs.queries.shared_resources.job_pins import job_resource_pins_tx
from server.app.storage import ObjectStorage, build_s3_storage
from shared.material_cache import MaterializeError
from shared.protocol import PROTOCOL_VERSION, SHARED_RESOURCE_PROTOCOL_VERSION
from shared.resource_manifest import (
    SHARED_RESOURCE_CLAIM_FORMAT,
    claim_resource_ids,
    validated_pin_files,
)


def _storage(storage: ObjectStorage | None) -> ObjectStorage:
    resolved = storage or build_s3_storage()
    if resolved is None:
        raise MaterializeError("shared resource storage is not configured on this instance")
    return resolved


def shared_resource_claim_block(
    connect_source: ConnectSource,
    workspace_id: str,
    job: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    storage: ObjectStorage | None = None,
    download_expires_seconds: int = 3600,
    worker_protocol_version: int = PROTOCOL_VERSION,
) -> dict[str, Any] | None:
    resource_ids = claim_resource_ids(manifest, job.get("workflow_definition_snapshot_json"))
    if not resource_ids:
        return None
    if worker_protocol_version < SHARED_RESOURCE_PROTOCOL_VERSION:
        raise MaterializeError(
            f"shared resources require Worker protocol v{SHARED_RESOURCE_PROTOCOL_VERSION}+;"
            f" this Worker speaks v{worker_protocol_version}"
        )
    job_workspace = str(job.get("workspace_id") or workspace_id)
    if job_workspace != workspace_id:
        raise MaterializeError("shared resource job workspace does not match the claim")
    with read_connection(connect_source) as conn:
        pins = job_resource_pins_tx(conn, str(job["id"]))
    by_id = {str(pin["resource_id"]): pin for pin in pins}
    if len(by_id) != len(pins):
        raise MaterializeError("shared resource job contains duplicate pins")
    if set(by_id).intersection(resource_ids) != set(resource_ids):
        raise MaterializeError("shared resource job pin does not cover the node declaration")

    prepared: list[tuple[Mapping[str, Any], list[dict[str, Any]], dict[str, str]]] = []
    for resource_id in resource_ids:
        pin = by_id[resource_id]
        try:
            files = validated_pin_files(pin)
            version = pin["version"]
            if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
                raise ValueError("shared resource descriptor has an invalid version")
            original_files = {str(file["path"]): file for file in pin["files"]}
            storage_keys: dict[str, str] = {}
            for file in files:
                storage_key = original_files[file["path"]].get("storage_key")
                if not isinstance(storage_key, str) or not storage_key:
                    raise ValueError("shared resource file has no target object")
                storage_keys[file["path"]] = storage_key
        except (KeyError, TypeError, ValueError) as exc:
            raise MaterializeError(
                f"shared resource descriptor failed verification: {resource_id}"
            ) from exc
        prepared.append((pin, files, storage_keys))

    object_storage = _storage(storage)
    response_pins = []
    for prepared_pin, files, storage_keys in prepared:
        resource_id = str(prepared_pin["resource_id"])
        response_pins.append(
            {
                "resource_id": resource_id,
                "version": int(prepared_pin["version"]),
                "manifest_hash": str(prepared_pin["manifest_hash"]),
                "files": [
                    {
                        **file,
                        "download_url": object_storage.presign_get(
                            storage_keys[file["path"]], expires_seconds=download_expires_seconds
                        ),
                    }
                    for file in files
                ],
            }
        )
    return {"format": SHARED_RESOURCE_CLAIM_FORMAT, "pins": response_pins}
