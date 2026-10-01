"""Stage request-owned objects and atomically publish their material rows.

Unique object keys make compensation safe even against concurrent uploads
of identical bytes/names. No other request can adopt our key before the
batch commits. PostgreSQL remains the authority for content deduplication.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from server.app.services.job_errors import ConflictError
from server.app.services.material_ttl import materials_ttl_days
from server.app.services.materials import MaterialStorageUnavailableError
from server.app.storage import ObjectStorage

logger = logging.getLogger(__name__)


def _cleanup(job_db: Any, storage: ObjectStorage, workspace_id: str, keys: list[str]) -> None:
    if not keys:
        return
    try:
        referenced = job_db.referenced_inline_objects(workspace_id, keys)
    except Exception:
        # #204 broad-except audit: commit outcome may be unknown. Preserve
        # objects rather than destroy committed data if the verification read
        # fails; log the exact cleanup set for recovery, never mask the cause.
        logger.exception("Cannot verify text object ownership; retaining %s", keys)
        return
    for key in keys:
        if key in referenced:
            continue
        try:
            storage.delete_object(key)
        except (ClientError, BotoCoreError, OSError):
            logger.exception("Text object cleanup failed: %s", key)


def store_text_batch(
    job_db: Any,
    storage: ObjectStorage,
    workspace_id: str,
    prepared: list[tuple[int, str, bytes]],
    created_by: str,
) -> dict[int, str]:
    """All puts precede one row transaction; compensate failures and race losers."""
    entries: dict[str, dict[str, Any]] = {}
    keys: list[str] = []
    indices: dict[int, str] = {}
    try:
        for index, filename, payload in prepared:
            digest = hashlib.sha256(payload).hexdigest()
            indices[index] = digest
            if digest in entries:
                continue
            existing = job_db.find_material_by_hash(workspace_id, digest)
            if existing is not None:
                if existing["status"] != "ready":
                    raise ConflictError(
                        f"Material {existing['id']} is {existing['status']}; "
                        "finish the upload or remove the non-ready material before retrying"
                    )
                entries[digest] = dict(content_hash=digest, material_id=existing["id"])
                continue
            key = f"{workspace_id}/{digest}/inline-{uuid.uuid4().hex}"
            content_type = (
                "text/markdown; charset=utf-8"
                if filename.lower().endswith(".md")
                else "text/plain; charset=utf-8"
            )
            # Register before PUT: a timeout can occur after the server stored it.
            keys.append(key)
            storage.put_object(key, payload, content_type=content_type)
            entries[digest] = dict(
                content_hash=digest,
                filename=filename,
                content_type=content_type,
                size_bytes=len(payload),
                storage_key=key,
            )
        identities = job_db.publish_inline_materials(
            workspace_id,
            list(entries.values()),
            created_by=created_by,
            ttl_days=materials_ttl_days(job_db),
        )
    except (ClientError, BotoCoreError, OSError) as exc:
        raise MaterialStorageUnavailableError(
            "Material storage is unreachable; text items could not be stored"
        ) from exc
    finally:
        # Also runs after arbitrary DB errors; the original traceback survives.
        _cleanup(job_db, storage, workspace_id, keys)
    return {index: identities[digest] for index, digest in indices.items()}
