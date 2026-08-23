"""Materials service (materials-and-runs design §5.1/§6.4).

Materials are browser-uploaded files: bytes live in the instance
S3-compatible object store, metadata in the ``materials`` table. Upload
follows presign → direct PUT → complete; complete verifies the stored object
against the declaration (size always, sha256 when the client declared one)
before marking the row ready. Rows are content-deduped per workspace on
declared content_hash.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.job_errors import (
    ConflictError,
    JobServiceError,
    NotFoundError,
)
from server.app.storage import ObjectStorage

_PRESIGN_EXPIRY_SECONDS = 3600
_HASH_CHUNK_BYTES = 1024 * 1024
_DEFAULT_LIST_LIMIT = 50
_MAX_LIST_LIMIT = 200


class MaterialStorageUnavailableError(JobServiceError):
    """Object storage is not configured on this instance (routes map to 503)."""


class MaterialVerificationError(JobServiceError):
    """Stored object does not match the declared size/hash (routes map to 422)."""


class MaterialInUseError(JobServiceError):
    """Material is still referenced by a job input (routes map to 409)."""


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _record(row: dict[str, Any]) -> dict[str, Any]:
    """Public material record; the internal storage_key never leaves the service."""
    return {
        "id": str(row["id"]),
        "workspace_id": str(row["workspace_id"]),
        "content_hash": str(row["content_hash"]),
        "filename": str(row["filename"]),
        "content_type": str(row["content_type"]),
        "size_bytes": int(row["size_bytes"]),
        "status": str(row["status"]),
        "created_by": str(row["created_by"]),
        "created_at": _timestamp(row["created_at"]),
        "expires_at": _timestamp(row["expires_at"]),
    }


class MaterialsService:
    def __init__(self, database_dsn: DatabaseDsn, storage: ObjectStorage | None = None) -> None:
        self._dsn = database_dsn
        # Public seam: tests inject a fake ObjectStorage; an unconfigured
        # instance keeps None and the API degrades to 503.
        self.storage = storage

    def _require_storage(self) -> ObjectStorage:
        if self.storage is None:
            raise MaterialStorageUnavailableError(
                "Material storage is not configured on this instance "
                "(AGENT_LEGION_S3_BUCKET is unset)"
            )
        return self.storage

    def _fetch_row(self, workspace_id: str, material_id: str) -> dict[str, Any]:
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                "select * from materials where id=%s and workspace_id=%s",
                (material_id, workspace_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Material not found: {material_id}")
        return dict(row)

    def presign(
        self,
        workspace_id: str,
        *,
        filename: str,
        size_bytes: int,
        content_type: str = "",
        content_hash: str = "",
        created_by: str = "",
    ) -> dict[str, Any]:
        """Create/reuse an uploading row and return a presigned PUT URL.

        A ready row with the same declared content_hash is returned as-is
        (upload dedup, no URL); a stale uploading/failed row with the same
        hash is reset to uploading so the upload can be retried.
        """
        storage = self._require_storage()
        content_hash = content_hash.strip()
        deduplicated = False
        with write_transaction(self._dsn) as conn:
            row = None
            if content_hash:
                row = conn.execute(
                    "select * from materials where workspace_id=%s and content_hash=%s",
                    (workspace_id, content_hash),
                ).fetchone()
            if row is None:
                material_id = uuid.uuid4().hex
                storage_key = f"{workspace_id}/{content_hash or material_id}/{filename}"
                insert_sql = """
                    insert into materials(
                      id, workspace_id, content_hash, filename, content_type,
                      size_bytes, storage_key, status, created_by
                    ) values (%s, %s, %s, %s, %s, %s, %s, 'uploading', %s)
                    """
                insert_params = (
                    material_id,
                    workspace_id,
                    content_hash,
                    filename,
                    content_type,
                    size_bytes,
                    storage_key,
                    created_by,
                )
                if content_hash:
                    # A concurrent presign of the same content may have won the
                    # race since the select above: fall back to its row.
                    inserted = conn.execute(
                        insert_sql + " on conflict (workspace_id, content_hash)"
                        " where content_hash <> '' do nothing returning id",
                        insert_params,
                    ).fetchone()
                    if inserted is None:
                        row = conn.execute(
                            "select * from materials where workspace_id=%s and content_hash=%s",
                            (workspace_id, content_hash),
                        ).fetchone()
                else:
                    conn.execute(insert_sql, insert_params)
            if row is not None:
                material_id = str(row["id"])
                storage_key = str(row["storage_key"])
                if str(row["status"]) == "ready":
                    deduplicated = True
                else:
                    conn.execute(
                        """
                        update materials
                        set filename=%s, content_type=%s, size_bytes=%s, status='uploading'
                        where id=%s
                        """,
                        (filename, content_type, size_bytes, material_id),
                    )
        upload_url: str | None = None
        if not deduplicated:
            upload_url = storage.presign_put(
                storage_key, size_bytes, expires_seconds=_PRESIGN_EXPIRY_SECONDS
            )
        material = _record(self._fetch_row(workspace_id, material_id))
        return {
            "material": material,
            "upload_url": upload_url,
            "upload_expires_in_seconds": _PRESIGN_EXPIRY_SECONDS,
            "deduplicated": deduplicated,
        }

    def complete(self, workspace_id: str, material_id: str) -> dict[str, Any]:
        """Verify the stored object against the declaration and mark ready.

        Verification failures flip the row to failed so the client can retry
        the upload (presign resets the row to uploading).
        """
        storage = self._require_storage()
        row = self._fetch_row(workspace_id, material_id)
        if row["status"] == "ready":
            return _record(row)
        if row["status"] == "expired":
            raise ConflictError(f"Material is expired: {material_id}")
        failure = self._verify_object(storage, row)
        with write_transaction(self._dsn) as conn:
            if failure is not None:
                conn.execute(
                    "update materials set status='failed' where id=%s",
                    (material_id,),
                )
            else:
                conn.execute(
                    "update materials set status='ready' where id=%s",
                    (material_id,),
                )
        if failure is not None:
            raise MaterialVerificationError(failure)
        return _record(self._fetch_row(workspace_id, material_id))

    def _verify_object(self, storage: ObjectStorage, row: dict[str, Any]) -> str | None:
        """Return a failure detail, or None when the object matches the row."""
        storage_key = str(row["storage_key"])
        head = storage.head_object(storage_key)
        if head is None:
            return f"Uploaded object is missing for material {row['id']}"
        declared_size = int(row["size_bytes"])
        if head.size_bytes != declared_size:
            return (
                f"Uploaded object size {head.size_bytes} does not match "
                f"the declared size {declared_size}"
            )
        content_hash = str(row["content_hash"])
        if content_hash:
            digest = hashlib.sha256()
            stream = storage.open_stream(storage_key)
            while True:
                chunk = stream.read(_HASH_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
            actual = digest.hexdigest()
            if actual != content_hash:
                return (
                    f"Uploaded object sha256 {actual} does not match "
                    f"the declared hash {content_hash}"
                )
        return None

    def list(self, workspace_id: str, *, limit: int, offset: int) -> dict[str, Any]:
        limit = max(1, min(limit or _DEFAULT_LIST_LIMIT, _MAX_LIST_LIMIT))
        offset = max(0, offset)
        with read_connection(self._dsn) as conn:
            total_row = conn.execute(
                "select count(*) as total from materials where workspace_id=%s",
                (workspace_id,),
            ).fetchone()
            rows = conn.execute(
                """
                select * from materials where workspace_id=%s
                order by created_at desc, id desc limit %s offset %s
                """,
                (workspace_id, limit, offset),
            ).fetchall()
        total = int(total_row["total"]) if total_row is not None else 0
        return {
            "materials": [_record(dict(row)) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def get(self, workspace_id: str, material_id: str) -> dict[str, Any]:
        return _record(self._fetch_row(workspace_id, material_id))

    def delete(self, workspace_id: str, material_id: str) -> None:
        """Delete the object and its row unless a job input references it."""
        storage = self._require_storage()
        # 检查与删除在同一事务，先 FOR UPDATE 锁材料行，与 run 创建侧
        # （create_jobs_bulk 对引用材料行的 FOR KEY SHARE）串行化：delete 先
        # 持锁则 run 侧阻塞至行消失后报错；run 侧先持锁则 delete 阻塞至 job
        # 插入提交，下面的引用检查随之看到它。
        # v1 blunt guard: any referencing job blocks deletion — queued jobs,
        # failure re-runs and quality replays re-resolve the row at dispatch
        # time and would fail with "material not found". Design §10 reference
        # counting replaces this guard in a later slice.
        with write_transaction(self._dsn) as conn:
            row = conn.execute(
                "select * from materials where id=%s and workspace_id=%s for update",
                (material_id, workspace_id),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Material not found: {material_id}")
            referencing = conn.execute(
                "select id from jobs where workspace_id=%s"
                " and input_json::jsonb ->> 'type' = 'material'"
                " and input_json::jsonb ->> 'material_id' = %s limit 1",
                (workspace_id, material_id),
            ).fetchone()
            if referencing is not None:
                raise MaterialInUseError(f"Material is referenced by job {referencing['id']}")
            # 先删对象再删行（保持既有顺序）：对象删除失败则事务回滚、行仍在
            # 可重试；行已删而对象残留才是不可恢复方向。
            storage.delete_object(str(row["storage_key"]))
            conn.execute("delete from materials where id=%s", (material_id,))
