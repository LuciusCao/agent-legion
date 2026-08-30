"""Host-side bundle materialization for code-node dispatch (design §6.2, #156).

A ``bundle`` job input references a manifest row; the dispatching
parent materializes every member into the content-addressed cache (per-file
dedup intact, same as single materials) and assembles the folder's
directory tree (``shared/material_bundle.py``), handing node code the tree
root through ``runtime["materials"]`` with ``kind: "bundle"``. The Worker
claim path gets one presigned GET per member — ``storage_key`` never
crosses the wire, same as single materials.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import Any

from server.app.db.dialect import ConnectSource
from server.app.db.transaction import read_connection
from server.app.services.material_cache import _require_storage, input_document
from server.app.storage import ObjectStorage
from shared.material_bundle import assemble_bundle_tree, bundle_address
from shared.material_cache import MaterializeError, cache_file_path, materialize_stream

logger = logging.getLogger(__name__)


def is_bundle_input(job: Mapping[str, Any]) -> bool:
    """True when the job's input document is a bundle item."""
    return input_document(job).get("type") == "bundle"


def _ready_bundle(
    connect_source: ConnectSource, workspace_id: str, input_doc: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The validated (existing, all-members-ready) bundle and member rows."""
    bundle_id = str(input_doc.get("bundle_id") or "").strip()
    if not bundle_id:
        raise MaterializeError("bundle job input is missing bundle_id")
    with read_connection(connect_source) as conn:
        bundle = conn.execute(
            "select * from material_bundles where id=%s and workspace_id=%s",
            (bundle_id, workspace_id),
        ).fetchone()
        if bundle is None:
            raise MaterializeError(f"material bundle not found: {bundle_id}")
        members = [
            dict(row)
            for row in conn.execute(
                "select m.path, mat.id, mat.status, mat.filename, mat.size_bytes,"
                " mat.content_hash, mat.storage_key"
                " from material_bundle_members m"
                " join materials mat on mat.id = m.material_id"
                " where m.bundle_id=%s order by m.ordinal",
                (bundle_id,),
            ).fetchall()
        ]
    for member in members:
        status = str(member.get("status") or "")
        if status != "ready":
            raise MaterializeError(f"bundle member {member['id']} is not ready (status: {status})")
    return dict(bundle), members


def _member_address(member: Mapping[str, Any]) -> str:
    """Same addressing rule as single materials: content hash, else id."""
    return str(member.get("content_hash") or "") or str(member["id"])


def _entries_block(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "path": str(member["path"]),
            "size_bytes": int(member.get("size_bytes") or 0),
            "content_hash": str(member.get("content_hash") or ""),
        }
        for member in members
    ]


def bundle_runtime_block(
    connect_source: ConnectSource,
    cache_root: Path,
    workspace_id: str,
    job: Mapping[str, Any],
    *,
    storage: ObjectStorage | None = None,
) -> dict[str, Any] | None:
    """The ``runtime["materials"]`` block for a bundle-input job, else None."""
    input_doc = input_document(job)
    if input_doc.get("type") != "bundle":
        return None
    bundle, members = _ready_bundle(connect_source, workspace_id, input_doc)
    bundle_id = str(bundle["id"])
    storage = _require_storage(storage, bundle_id)
    manifest = [(_member_address(member), str(member["path"])) for member in members]
    address = bundle_address(manifest)
    # 全程 pin 住全部成员与目录树：成员 i+1 的淘汰回合不得删掉成员 i，
    # 否则组装在收尾时撞 FileNotFoundError（pin 语义见 materialize_stream）。
    pin = {cache_file_path(cache_root, member_address) for member_address, _ in manifest}
    pin.add(cache_file_path(cache_root, address))
    materialized: list[tuple[Path, str]] = []
    for member, (member_address, relpath) in zip(members, manifest, strict=True):
        try:
            path = materialize_stream(
                cache_root,
                member_address,
                partial(storage.open_stream, str(member["storage_key"])),
                expected_sha256=str(member.get("content_hash") or ""),
                expected_size=int(member.get("size_bytes") or 0),
                pin=pin,
                log=lambda message: logger.warning("%s", message),
            )
        except MaterializeError:
            raise
        except Exception as exc:
            raise MaterializeError(
                f"failed to materialize bundle member {member['id']}: {exc}"
            ) from exc
        materialized.append((path, relpath))
    tree = assemble_bundle_tree(cache_root, address, materialized)
    return {
        "material_id": bundle_id,
        "kind": "bundle",
        "path": str(tree),
        "filename": str(bundle.get("name") or ""),
        "content_type": "",
        "size_bytes": int(bundle.get("total_size_bytes") or 0),
        "content_hash": address,
        "entries": _entries_block(members),
    }


def prefetch_bundle_block(
    executor: Any, job: Mapping[str, Any], workspace_id: str
) -> dict[str, Any] | None:
    """Executor-facing wrapper mirroring ``prefetch_material_block``."""
    if not is_bundle_input(job):
        return None
    job_db = executor.job_db
    if job_db is None:
        raise MaterializeError("bundle job input cannot be materialized without the job database")
    # Facade passthrough (#187, BOUNDARY-DATA-001): read_connection accepts
    # ConnectSource, so the executor's job_db handle goes straight through —
    # never unwrapped to a DSN via getattr.
    return bundle_runtime_block(
        job_db,
        executor._materials_cache_root,
        workspace_id,
        job,
        storage=executor._object_store(),
    )


def bundle_claim_block(
    connect_source: ConnectSource,
    workspace_id: str,
    job: Mapping[str, Any],
    *,
    storage: ObjectStorage | None = None,
    download_expires_seconds: int = 3600,
) -> dict[str, Any] | None:
    """The Worker-facing bundle descriptor: one presigned GET per member."""
    input_doc = input_document(job)
    if input_doc.get("type") != "bundle":
        return None
    bundle, members = _ready_bundle(connect_source, workspace_id, input_doc)
    bundle_id = str(bundle["id"])
    storage = _require_storage(storage, bundle_id)
    return {
        "material_id": bundle_id,
        "kind": "bundle",
        "filename": str(bundle.get("name") or ""),
        "size_bytes": int(bundle.get("total_size_bytes") or 0),
        "entries": [
            {
                "material_id": str(member["id"]),
                "path": str(member["path"]),
                "size_bytes": int(member.get("size_bytes") or 0),
                "content_hash": str(member.get("content_hash") or ""),
                "download_url": storage.presign_get(
                    str(member["storage_key"]), expires_seconds=download_expires_seconds
                ),
            }
            for member in members
        ],
    }
