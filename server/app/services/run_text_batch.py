"""Stage request-owned objects and atomically publish their material rows.

Writes go through the shared server-side chokepoint
(``material_object_publish``, #1314): uploading intent rows reference every
object key before its PUT, and one transaction swaps them for ready rows.
Unique object keys make compensation safe even against concurrent uploads
of identical bytes/names; PostgreSQL remains the authority for content
deduplication.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from server.app.services.job_errors import ConflictError
from server.app.services.material_object_publish import (
    MaterialObjectWrite,
    publish_material_objects,
)
from server.app.services.material_ttl import materials_ttl_days
from server.app.services.materials import MaterialStorageUnavailableError
from server.app.services.text_item_content_types import TEXT_CONTENT_TYPES
from server.app.storage import ObjectStorage


def text_content_type(filename: str) -> str:
    """The content type a validated text-item filename is stored with."""
    return TEXT_CONTENT_TYPES[filename[filename.rfind(".") :].lower()]


def store_text_batch(
    job_db: Any,
    storage: ObjectStorage,
    workspace_id: str,
    prepared: list[tuple[int, str, bytes]],
    created_by: str,
) -> dict[int, str]:
    """Precheck every hash, then write all new texts as one intent batch."""
    writes: dict[str, MaterialObjectWrite] = {}
    reuse: dict[str, str] = {}
    indices: dict[int, str] = {}
    for index, filename, payload in prepared:
        digest = hashlib.sha256(payload).hexdigest()
        indices[index] = digest
        if digest in writes or digest in reuse:
            continue
        existing = job_db.find_material_by_hash(workspace_id, digest)
        if existing is not None:
            if existing["status"] != "ready":
                raise ConflictError(
                    f"Material {existing['id']} is {existing['status']}; "
                    "finish the upload or remove the non-ready material before retrying"
                )
            reuse[digest] = existing["id"]
            continue
        # #1279 key 文法（v2g0 + m_{material_id}，由 chokepoint 派生）；
        # inline-{uuid4} 尾段保留产线标记。
        writes[digest] = MaterialObjectWrite(
            content_hash=digest,
            filename=filename,
            content_type=text_content_type(filename),
            payload=payload,
            key_tail=f"inline-{uuid.uuid4().hex}",
        )
    try:
        identities = publish_material_objects(
            job_db,
            storage,
            workspace_id,
            list(writes.values()),
            created_by=created_by,
            ttl_days=materials_ttl_days(job_db),
            reuse=reuse,
        )
    except (ClientError, BotoCoreError, OSError) as exc:
        raise MaterialStorageUnavailableError(
            "Material storage is unreachable; text items could not be stored"
        ) from exc
    return {index: str(identities[digest]) for index, digest in indices.items()}
