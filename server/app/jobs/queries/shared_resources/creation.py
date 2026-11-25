"""Transactional intents and publication for shared-resource version creation."""

from __future__ import annotations

import json
import uuid
from typing import Any

from server.app.services.shared_resources.keys import resource_file_key

from .source import source_matches_intent_tx


def version_with_files_tx(conn: Any, version_id: str) -> dict[str, Any] | None:
    version = conn.execute(
        "select * from shared_resource_versions where id=%s", (version_id,)
    ).fetchone()
    if version is None:
        return None
    files = conn.execute(
        "select * from shared_resource_files where version_id=%s order by ordinal", (version_id,)
    ).fetchall()
    return {"version": dict(version), "files": [dict(row) for row in files]}


def resource_creation_tx(conn: Any, workspace_id: str, token: str) -> dict[str, Any] | None:
    resource = conn.execute(
        "select * from shared_resources where owner_workspace_id=%s and create_token=%s",
        (workspace_id, token),
    ).fetchone()
    if resource is None:
        return None
    version = conn.execute(
        "select id from shared_resource_versions where resource_id=%s and version=1",
        (resource["id"],),
    ).fetchone()
    if version is None:
        raise RuntimeError("shared resource creation intent has no initial version")
    intent = version_with_files_tx(conn, str(version["id"]))
    if intent is None:
        raise RuntimeError("shared resource initial version disappeared")
    return {"resource": dict(resource), **intent, "created": False}


def version_creation_tx(conn: Any, resource_id: str, token: str) -> dict[str, Any] | None:
    version = conn.execute(
        "select id from shared_resource_versions where resource_id=%s and idempotency_key=%s",
        (resource_id, token),
    ).fetchone()
    if version is None:
        return None
    intent = version_with_files_tx(conn, str(version["id"]))
    if intent is None:
        raise RuntimeError("shared resource version intent disappeared")
    return intent


def _new_file_rows(
    resource_id: str, version_id: str, source_files: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for ordinal, source in enumerate(source_files):
        file_id = uuid.uuid4().hex
        rows.append(
            {
                **source,
                "id": file_id,
                "ordinal": ordinal,
                "storage_key": resource_file_key(resource_id, version_id, 0, file_id),
            }
        )
    return rows


def _insert_version_and_files_tx(
    conn: Any,
    *,
    resource_id: str,
    version: int,
    token: str,
    request_hash: str,
    manifest_hash: str,
    source_snapshot: dict[str, Any],
    created_by: str,
) -> dict[str, Any]:
    version_id = uuid.uuid4().hex
    source_json = json.dumps(source_snapshot, sort_keys=True, separators=(",", ":"))
    conn.execute(
        "insert into shared_resource_versions("
        " id, resource_id, version, status, source_type, source_id, source_workspace_id,"
        " source_snapshot_json, manifest_hash, idempotency_key, request_hash, created_by"
        ") values (%s, %s, %s, 'copying', %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            version_id,
            resource_id,
            version,
            source_snapshot["source_type"],
            source_snapshot["source_id"],
            source_snapshot["source_workspace_id"],
            source_json,
            manifest_hash,
            token,
            request_hash,
            created_by,
        ),
    )
    files = _new_file_rows(resource_id, version_id, source_snapshot["files"])
    for file in files:
        conn.execute(
            "insert into shared_resource_files("
            " id, version_id, ordinal, path, sha256, size_bytes, content_type, storage_key,"
            " source_storage_key, source_material_id, source_upload_generation, source_etag"
            ") values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                file["id"],
                version_id,
                file["ordinal"],
                file["path"],
                file["sha256"],
                file["size_bytes"],
                file["content_type"],
                file["storage_key"],
                file["source_storage_key"],
                file["source_material_id"],
                file["source_upload_generation"],
                file["source_etag"],
            ),
        )
    intent = version_with_files_tx(conn, version_id)
    if intent is None:
        raise RuntimeError("new shared resource version intent is missing")
    return intent


def insert_resource_intent_tx(
    conn: Any,
    *,
    workspace_id: str,
    name: str,
    token: str,
    request_hash: str,
    manifest_hash: str,
    source_snapshot: dict[str, Any],
    created_by: str,
) -> dict[str, Any]:
    resource_id = uuid.uuid4().hex
    conn.execute(
        "insert into shared_resources("
        " id, owner_workspace_id, name, create_token, create_request_hash, created_by"
        ") values (%s, %s, %s, %s, %s, %s)",
        (resource_id, workspace_id, name, token, request_hash, created_by),
    )
    intent = _insert_version_and_files_tx(
        conn,
        resource_id=resource_id,
        version=1,
        token=token,
        request_hash=request_hash,
        manifest_hash=manifest_hash,
        source_snapshot=source_snapshot,
        created_by=created_by,
    )
    grant_id = uuid.uuid4().hex
    conn.execute(
        "insert into shared_resource_grants(id, resource_id, workspace_id, status, created_by)"
        " values (%s, %s, %s, 'active', %s)",
        (grant_id, resource_id, workspace_id, created_by),
    )
    conn.execute(
        "insert into shared_resource_selections("
        " grant_id, workspace_id, resource_id, version_id, revision, selected_by"
        ") values (%s, %s, %s, %s, 1, %s)",
        (grant_id, workspace_id, resource_id, intent["version"]["id"], created_by),
    )
    resource = conn.execute("select * from shared_resources where id=%s", (resource_id,)).fetchone()
    if resource is None:
        raise RuntimeError("new shared resource row is missing")
    return {"resource": dict(resource), **intent, "created": True}


def insert_version_intent_tx(
    conn: Any,
    *,
    resource_id: str,
    token: str,
    request_hash: str,
    manifest_hash: str,
    source_snapshot: dict[str, Any],
    created_by: str,
) -> dict[str, Any] | None:
    resource = conn.execute(
        "select * from shared_resources where id=%s for update", (resource_id,)
    ).fetchone()
    if resource is None:
        return None
    existing = version_creation_tx(conn, resource_id, token)
    if existing is not None:
        return existing
    row = conn.execute(
        "select coalesce(max(version), 0) + 1 as next_version from shared_resource_versions"
        " where resource_id=%s",
        (resource_id,),
    ).fetchone()
    next_version = int(row["next_version"])
    return _insert_version_and_files_tx(
        conn,
        resource_id=resource_id,
        version=next_version,
        token=token,
        request_hash=request_hash,
        manifest_hash=manifest_hash,
        source_snapshot=source_snapshot,
        created_by=created_by,
    )


def restart_version_attempt_tx(conn: Any, version_id: str) -> dict[str, Any] | None:
    """Move a retry to fresh, single-use keys; a ready version is returned as-is."""
    version = conn.execute(
        "select * from shared_resource_versions where id=%s for update", (version_id,)
    ).fetchone()
    if version is None:
        return None
    if str(version["status"]) == "ready":
        return version_with_files_tx(conn, version_id)
    generation = int(version["copy_generation"]) + 1
    changed = conn.execute(
        "update shared_resource_versions set status='copying', copy_generation=%s,"
        " failure_code='', failure_detail='' where id=%s and status in ('copying', 'failed')",
        (generation, version_id),
    )
    if changed.rowcount != 1:
        return version_with_files_tx(conn, version_id)
    files = conn.execute(
        "select id from shared_resource_files where version_id=%s order by ordinal", (version_id,)
    ).fetchall()
    for file in files:
        conn.execute(
            "update shared_resource_files set storage_key=%s where id=%s and version_id=%s",
            (
                resource_file_key(
                    str(version["resource_id"]), version_id, generation, str(file["id"])
                ),
                file["id"],
                version_id,
            ),
        )
    return version_with_files_tx(conn, version_id)


def mark_version_failed_tx(
    conn: Any, version_id: str, generation: int, code: str, detail: str
) -> None:
    conn.execute(
        "update shared_resource_versions set status='failed', failure_code=%s, failure_detail=%s"
        " where id=%s and copy_generation=%s and status='copying'",
        (code, detail, version_id, generation),
    )


def publish_version_tx(conn: Any, version_id: str, generation: int) -> dict[str, Any] | None:
    """Publish only the exact verified attempt and a still-current source snapshot."""
    version = conn.execute(
        "select * from shared_resource_versions where id=%s for update", (version_id,)
    ).fetchone()
    if version is None:
        return None
    if str(version["status"]) == "ready":
        return version_with_files_tx(conn, version_id)
    if int(version["copy_generation"]) != generation or str(version["status"]) != "copying":
        return None
    if not source_matches_intent_tx(conn, dict(version)):
        return None
    conn.execute(
        "update shared_resource_versions set status='ready', ready_at=current_timestamp,"
        " failure_code='', failure_detail='' where id=%s and copy_generation=%s"
        " and status='copying'",
        (version_id, generation),
    )
    return version_with_files_tx(conn, version_id)
