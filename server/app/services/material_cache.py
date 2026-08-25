"""Host-side material materialization for code-node dispatch (design §6.2).

When a job's ``input_json`` is a material item, the dispatching parent
(this module, via ``build_runtime``) materializes the object into the local
content-addressed cache and hands node code the local path through
``runtime["materials"]`` — the node itself never fetches (the sandbox
denies network, EXEC-CODE-003) and the cache root is a static sandbox
allow-read entry (MATERIAL-ACCESS-001). The cache mechanics (layout,
atomic rename, LRU eviction) are shared with the Worker in
``shared/material_cache.py``; this module only adds the DB row lookup and
the S3 byte source.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from server.app.db.transaction import read_connection
from server.app.storage import ObjectStorage, build_s3_storage
from shared.material_cache import MaterializeError, materialize_stream

logger = logging.getLogger(__name__)


def _input_document(job: Mapping[str, Any]) -> dict[str, Any]:
    """Parsed ``jobs.input_json``; anything unreadable degrades to ``{}``."""
    raw = job.get("input_json")
    if isinstance(raw, Mapping):
        return dict(raw)
    try:
        value = json.loads(str(raw or ""))
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def is_material_input(job: Mapping[str, Any]) -> bool:
    """True when the job's input document is a material item."""
    return _input_document(job).get("type") == "material"


def _fetch_material_row(database_dsn: Any, workspace_id: str, material_id: str) -> dict[str, Any]:
    with read_connection(database_dsn) as conn:
        row = conn.execute(
            "select * from materials where id=%s and workspace_id=%s",
            (material_id, workspace_id),
        ).fetchone()
    if row is None:
        raise MaterializeError(f"material not found: {material_id}")
    return dict(row)


def _ready_row(
    database_dsn: Any, workspace_id: str, input_doc: Mapping[str, Any]
) -> dict[str, Any]:
    """The validated (existing, ready) material row for an input document."""
    material_id = str(input_doc.get("material_id") or "").strip()
    if not material_id:
        raise MaterializeError("material job input is missing material_id")
    row = _fetch_material_row(database_dsn, workspace_id, material_id)
    status = str(row.get("status") or "")
    if status != "ready":
        raise MaterializeError(f"material {material_id} is not ready (status: {status})")
    return row


def _require_storage(storage: ObjectStorage | None, material_id: str) -> ObjectStorage:
    if storage is None:
        storage = build_s3_storage()
    if storage is None:
        raise MaterializeError(
            "material storage is not configured on this instance "
            "(AGENT_LEGION_S3_BUCKET is unset); cannot materialize "
            f"material {material_id}"
        )
    return storage


def material_runtime_block(
    database_dsn: Any,
    cache_root: Path,
    workspace_id: str,
    job: Mapping[str, Any],
    *,
    storage: ObjectStorage | None = None,
) -> dict[str, Any] | None:
    """The ``runtime["materials"]`` block for a material-input job, else None.

    ``storage`` is the test seam (a fake ObjectStorage); when omitted the
    instance env resolves it, and an unconfigured instance raises a clear
    node-facing error instead of failing obscurely.
    """
    input_doc = _input_document(job)
    if input_doc.get("type") != "material":
        return None
    row = _ready_row(database_dsn, workspace_id, input_doc)
    material_id = str(row["id"])
    storage = _require_storage(storage, material_id)
    content_hash = str(row.get("content_hash") or "")
    filename = str(row.get("filename") or "")
    size_bytes = int(row.get("size_bytes") or 0)
    # Content addressing dedups downloads across jobs of the same material;
    # hashless rows fall back to their (unique) id.
    address = content_hash or material_id
    try:
        path = materialize_stream(
            cache_root,
            address,
            lambda: storage.open_stream(str(row["storage_key"])),
            expected_sha256=content_hash,
            expected_size=size_bytes,
            log=lambda message: logger.warning("%s", message),
        )
    except MaterializeError:
        raise
    except Exception as exc:
        raise MaterializeError(f"failed to materialize material {material_id}: {exc}") from exc
    return {
        "material_id": material_id,
        "path": str(path),
        "filename": filename,
        "content_type": str(row.get("content_type") or ""),
        "size_bytes": size_bytes,
        "content_hash": content_hash,
    }


def prefetch_material_block(
    executor: Any, job: Mapping[str, Any], workspace_id: str
) -> dict[str, Any] | None:
    """Executor-facing wrapper: materialize a material-type job input (design §6.2).

    The dispatching parent downloads here so the sandboxed node only reads a
    local file (EXEC-CODE-003); failures raise ``MaterializeError`` with a
    node-facing message, which the sandbox path maps to a failed result.
    Bundle inputs materialize as a directory tree (#156, see
    ``material_bundle_cache``).
    """
    from server.app.services.material_bundle_cache import prefetch_bundle_block

    bundle_block = prefetch_bundle_block(executor, job, workspace_id)
    if bundle_block is not None:
        return bundle_block
    if not is_material_input(job):
        return None
    job_db = executor.job_db
    dsn = str(getattr(job_db, "path", "") or "") if job_db is not None else ""
    if not dsn:
        raise MaterializeError("material job input cannot be materialized without the job database")
    return material_runtime_block(
        dsn,
        executor._materials_cache_root,
        workspace_id,
        job,
        storage=executor._object_store(),
    )


def material_claim_block(
    database_dsn: Any,
    workspace_id: str,
    job: Mapping[str, Any],
    *,
    storage: ObjectStorage | None = None,
    download_expires_seconds: int = 3600,
) -> dict[str, Any] | None:
    """The Worker-facing material descriptor for the claim response.

    Injected into the claim-time ``runtime_context`` (memory only, like the
    secret injection — the persisted manifest keeps only the audit stub).
    The Worker materializes from the presigned GET URL with no storage
    credentials of its own; ``storage_key`` never crosses the wire. Bundle
    inputs get one presigned GET per member (#156).
    """
    input_doc = _input_document(job)
    if input_doc.get("type") == "bundle":
        from server.app.services.material_bundle_cache import bundle_claim_block

        return bundle_claim_block(
            database_dsn,
            workspace_id,
            job,
            storage=storage,
            download_expires_seconds=download_expires_seconds,
        )
    if input_doc.get("type") != "material":
        return None
    row = _ready_row(database_dsn, workspace_id, input_doc)
    material_id = str(row["id"])
    storage = _require_storage(storage, material_id)
    return {
        "material_id": material_id,
        "filename": str(row.get("filename") or ""),
        "content_type": str(row.get("content_type") or ""),
        "size_bytes": int(row.get("size_bytes") or 0),
        "content_hash": str(row.get("content_hash") or ""),
        "download_url": storage.presign_get(
            str(row["storage_key"]), expires_seconds=download_expires_seconds
        ),
    }
