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
from server.app.services.text_item_content_types import TEXT_CONTENT_TYPES
from server.app.storage import ObjectStorage

logger = logging.getLogger(__name__)


def text_content_type(filename: str) -> str:
    """The content type a validated text-item filename is stored with."""
    return TEXT_CONTENT_TYPES[filename[filename.rfind(".") :].lower()]


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
            # #1279 key 文法：v2g 代次段 + m_{material_id} 标记唯一段（与上传
            # 产线同文法），跨行不可复用——GC 材料删除批重开（#1314）后
            # inline 孤儿即可回收；inline-{uuid4} 尾段保留产线标记。
            # material_id 在此预生成、随 entry 传给 publish。
            material_id = uuid.uuid4().hex
            key = f"{workspace_id}/{digest}/v2g0/m_{material_id}/inline-{uuid.uuid4().hex}"
            content_type = text_content_type(filename)
            # Register before PUT: a timeout can occur after the server stored it.
            keys.append(key)
            storage.put_object(key, payload, content_type=content_type)
            # put 后 HEAD 取 ETag 一并落列（#1279：直插 ready 产线同样携带
            # 内容身份）；put 成功而 HEAD 读不到对象或**空 ETag**（R3b：
            # 无法建立身份）都是存储面故障，按不可用处理——不得落 ready
            # 且无身份的行。
            head = storage.head_object(key)
            if head is None or not head.etag:
                raise OSError(f"head after put returned no object/etag: {key}")
            entries[digest] = dict(
                content_hash=digest,
                filename=filename,
                content_type=content_type,
                size_bytes=len(payload),
                storage_key=key,
                new_material_id=material_id,
                object_etag=head.etag,
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
