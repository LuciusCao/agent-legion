"""Source snapshots and locked publication rechecks for shared resources."""

from __future__ import annotations

from typing import Any

from server.app.services.material_bundles import validate_member_path

_MATERIAL_COLUMNS = (
    "id, workspace_id, content_hash, filename, content_type, size_bytes, storage_key,"
    " status, object_etag, upload_generation"
)


def _file_from_material(row: dict[str, Any], *, path: str) -> dict[str, Any]:
    return {
        "path": validate_member_path(path),
        "sha256": str(row["content_hash"]),
        "declared_sha256": str(row["content_hash"]),
        "size_bytes": int(row["size_bytes"]),
        "content_type": str(row["content_type"]),
        "status": str(row["status"]),
        "source_storage_key": str(row["storage_key"]),
        "source_material_id": str(row["id"]),
        "source_upload_generation": int(row["upload_generation"]),
        "source_etag": str(row["object_etag"]),
    }


def load_source_snapshot_tx(
    conn: Any,
    workspace_id: str,
    source_type: str,
    source_id: str,
    *,
    for_publication: bool = False,
) -> dict[str, Any] | None:
    """Read one material/bundle snapshot, optionally locking it for publication.

    Paths enter their canonical form here so initial intent and publication
    recheck compare the same identity. ``FOR KEY SHARE`` serializes the final
    recheck with material/bundle DELETE; no lock is held while bytes are copied.
    """
    lock = " for key share" if for_publication else ""
    if source_type == "material":
        row = conn.execute(
            f"select {_MATERIAL_COLUMNS} from materials where workspace_id=%s and id=%s{lock}",
            (workspace_id, source_id),
        ).fetchone()
        if row is None:
            return None
        material = dict(row)
        return {
            "source_type": "material",
            "source_id": source_id,
            "source_workspace_id": workspace_id,
            "files": [_file_from_material(material, path=str(material["filename"]))],
        }
    if source_type != "bundle":
        return None
    bundle = conn.execute(
        f"select id, workspace_id, name from material_bundles"
        f" where workspace_id=%s and id=%s{lock}",
        (workspace_id, source_id),
    ).fetchone()
    if bundle is None:
        return None
    member_lock = " for key share of mat" if for_publication else ""
    rows = conn.execute(
        f"select m.path, m.ordinal, {_MATERIAL_COLUMNS.replace('id,', 'mat.id as id,', 1)}"
        " from material_bundle_members m join materials mat on mat.id=m.material_id"
        " where m.bundle_id=%s order by m.ordinal" + member_lock,
        (source_id,),
    ).fetchall()
    files = []
    for row in rows:
        values = dict(row)
        files.append(_file_from_material(values, path=str(values["path"])))
    return {
        "source_type": "bundle",
        "source_id": source_id,
        "source_workspace_id": workspace_id,
        "files": files,
    }


def _identity(file: dict[str, Any]) -> tuple[Any, ...]:
    return (
        file["path"],
        file["declared_sha256"],
        int(file["size_bytes"]),
        file["content_type"],
        file["status"],
        file["source_storage_key"],
        file["source_material_id"],
        int(file["source_upload_generation"]),
        file["source_etag"],
    )


def source_matches_intent_tx(conn: Any, version: dict[str, Any]) -> bool:
    current = load_source_snapshot_tx(
        conn,
        str(version["source_workspace_id"]),
        str(version["source_type"]),
        str(version["source_id"]),
        for_publication=True,
    )
    if current is None:
        return False
    import json

    intended = json.loads(str(version["source_snapshot_json"]))
    current_files = sorted(current["files"], key=lambda item: item["path"])
    intended_files = sorted(intended["files"], key=lambda item: item["path"])
    return [_identity(item) for item in current_files] == [
        _identity(item) for item in intended_files
    ]
