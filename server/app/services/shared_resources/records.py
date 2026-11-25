"""Public records and the internal exact-pin descriptor."""

from __future__ import annotations

import json
from typing import Any

from server.app.db.rowmap import iso_optional


def source_audit(version: dict[str, Any]) -> dict[str, Any]:
    snapshot = json.loads(str(version["source_snapshot_json"]))
    return {
        "type": str(version["source_type"]),
        "id": str(version["source_id"]),
        "workspace_id": str(version["source_workspace_id"]),
        "materials": [
            {
                "material_id": str(file["source_material_id"]),
                "path": str(file["path"]),
                "upload_generation": int(file["source_upload_generation"]),
                "sha256": str(file["sha256"]),
                "size_bytes": int(file["size_bytes"]),
            }
            for file in snapshot["files"]
        ],
    }


def file_record(file: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": str(file["path"]),
        "sha256": str(file["sha256"]),
        "size_bytes": int(file["size_bytes"]),
        "content_type": str(file["content_type"]),
    }


def version_summary(version: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(version["id"]),
        "resource_id": str(version["resource_id"]),
        "version": int(version["version"]),
        "manifest_hash": str(version["manifest_hash"]),
        "source": source_audit(version),
        "file_count": int(version.get("file_count") or 0),
        "total_size_bytes": int(version.get("total_size_bytes") or 0),
        "created_by": str(version["created_by"]),
        "created_at": iso_optional(version["created_at"]),
        "ready_at": iso_optional(version["ready_at"]),
    }


def version_detail(intent: dict[str, Any]) -> dict[str, Any]:
    version = intent["version"]
    record = version_summary(version)
    record["file_count"] = len(intent["files"])
    record["total_size_bytes"] = sum(int(file["size_bytes"]) for file in intent["files"])
    record["files"] = [file_record(file) for file in intent["files"]]
    return record


def resource_record(row: dict[str, Any], workspace_id: str) -> dict[str, Any]:
    selection = None
    if row.get("current_version_id") is not None:
        selection = {
            "version_id": str(row["current_version_id"]),
            "version": int(row["current_version"]),
            "manifest_hash": str(row["current_manifest_hash"]),
            "revision": int(row["selection_revision"]),
            "selected_by": str(row["selected_by"]),
            "updated_at": iso_optional(row["selected_at"]),
        }
    return {
        "id": str(row["id"]),
        "name": str(row["name"]),
        "owner_workspace_id": str(row["owner_workspace_id"]),
        "is_owner": str(row["owner_workspace_id"]) == workspace_id,
        "access_status": str(row["access_status"]),
        "version_count": int(row["version_count"]),
        "current_selection": selection,
        "created_by": str(row["created_by"]),
        "created_at": iso_optional(row["created_at"]),
    }


def grant_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "resource_id": str(row["resource_id"]),
        "workspace_id": str(row["workspace_id"]),
        "workspace_name": str(row.get("workspace_name") or ""),
        "status": str(row["status"]),
        "created_by": str(row["created_by"]),
        "created_at": iso_optional(row["created_at"]),
        "revoked_by": str(row["revoked_by"]),
        "revoked_at": iso_optional(row["revoked_at"]),
    }


def selection_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "workspace_id": str(row["workspace_id"]),
        "resource_id": str(row["resource_id"]),
        "version_id": str(row["version_id"]),
        "version": int(row["version"]),
        "manifest_hash": str(row["manifest_hash"]),
        "revision": int(row["revision"]),
        "selected_by": str(row["selected_by"]),
        "updated_at": iso_optional(row["updated_at"]),
    }


def exact_pin_descriptor(resolved: dict[str, Any]) -> dict[str, Any]:
    """Internal M43/M44 input; never serialize this directly in an API route."""
    version = resolved["version"]
    return {
        "resource_id": str(version["resource_id"]),
        "version_id": str(version["id"]),
        "version": int(version["version"]),
        "manifest_hash": str(version["manifest_hash"]),
        "grant_id": str(resolved["grant"]["id"]),
        "source": source_audit(version),
        "files": [
            {
                **file_record(file),
                "storage_key": str(file["storage_key"]),
            }
            for file in resolved["files"]
        ],
    }
