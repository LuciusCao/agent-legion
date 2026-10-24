"""Materials service (materials-and-runs design §5.1/§6.4).

Materials are browser-uploaded files: bytes live in the instance
S3-compatible object store, metadata in the ``materials`` table. Upload
follows presign → direct PUT → complete; complete verifies the stored object
against the declaration (size always, sha256 when the client declared one)
before marking the row ready. Rows are content-deduped per workspace on
declared content_hash.
"""

from __future__ import annotations

import uuid
from typing import Any

from botocore.exceptions import ClientError

from server.app.db.dialect import ConnectSource
from server.app.db.rowmap import iso_optional
from server.app.db.transaction import read_connection, write_transaction
from server.app.jobs.queries.material_locked_rereads import (
    assert_material_upload_identity_tx,
)
from server.app.jobs.queries.material_repair import repair_material_for_reupload_tx
from server.app.services.job_errors import (
    ConflictError,
    JobServiceError,
    NotFoundError,
)
from server.app.services.material_key_validation import (
    validate_material_content_hash,
    validate_material_filename,
    verify_stored_object,
)
from server.app.services.material_ttl import mark_ready
from server.app.storage import IF_NONE_MATCH_STAR, ObjectStorage, is_precondition_failed

_PRESIGN_EXPIRY_SECONDS = 3600
_DEFAULT_LIST_LIMIT = 50
_MAX_LIST_LIMIT = 200


class MaterialStorageUnavailableError(JobServiceError):
    """Object storage is not configured on this instance (routes map to 503)."""


class MaterialVerificationError(JobServiceError):
    """Stored object does not match the declared size/hash (routes map to 422)."""


class MaterialInUseError(JobServiceError):
    """Material is still referenced by a job input (routes map to 409)."""


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
        "upload_generation": int(row["upload_generation"]),
        "created_by": str(row["created_by"]),
        "created_at": iso_optional(row["created_at"]),
        "expires_at": iso_optional(row["expires_at"]),
    }


class MaterialsService:
    @property
    def connect_source(self) -> ConnectSource:
        """The bundles adjunct's connection source (#187 facade passthrough)."""
        return self._connect_source

    def __init__(self, database_dsn: ConnectSource, storage: ObjectStorage | None = None) -> None:
        # ``connect_source`` exposes the adjunct's connection source without
        # leaking the raw DSN (facade in production, #187).
        self._connect_source = database_dsn
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
        with read_connection(self._connect_source) as conn:
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
        hash is reset to uploading so the upload can be retried. Object keys
        live in a per-generation, per-row namespace the client's filename
        cannot forge (``{ws}/{hash|id}/v2g{generation}/m_{material_id}/{filename}``,
        first issue included); every retry bumps ``upload_generation``
        (schema v97) and moves the row to that generation's key, so a
        same-size retry stays visible to complete()'s locked identity
        recheck and every previously issued PUT URL points at a dead key —
        bytes can never be overwritten across generations (#1267 codex
        R3/R4/R5 P1). The ``v2g`` version marker sits in the server-derived
        third segment and ``m_{material_id}`` marks the row-unique fourth
        (#1279, schema v98; the marker belongs to a segment no writer can
        inject from a filename — codex r1/r2 P1 族), making every key
        single-use across row lifecycles: deleting the row and re-uploading
        the same file under the same name can never reproduce the key, which
        is what the S3 orphan GC's material delete batch gates on
        (``material_key_single_use`` in s3_jobs_gc, #1314). The row (and
        its key) commits BEFORE the PUT URL is signed, so every object this
        protocol writes is referenced by a row from its first byte — the
        other half of the delete batch's safety argument. Every issued PUT
        URL signs ``If-None-Match: *`` into SignedHeaders (#1321): the key
        is create-only at the storage layer, so replaying the URL after the
        first landed PUT (notably after complete()) fails with 412 instead
        of silently overwriting — consumption-side If-Match (#1279) drops to
        defense-in-depth; uploaders must send the header verbatim and treat
        a 412 on network retry as "already uploaded" (complete() stays the
        content authority).
        Pre-namespace rows (no ``g`` segment) keep their keys untouched
        until their first retry.
        """
        validate_material_filename(filename)
        content_hash = content_hash.strip()
        if content_hash:
            validate_material_content_hash(content_hash)
        storage = self._require_storage()
        deduplicated = False
        with write_transaction(self._connect_source) as conn:
            row = None
            if content_hash:
                row = conn.execute(
                    "select * from materials where workspace_id=%s and content_hash=%s",
                    (workspace_id, content_hash),
                ).fetchone()
            if row is None:
                material_id = uuid.uuid4().hex
                storage_key = (
                    f"{workspace_id}/{content_hash or material_id}/v2g0/m_{material_id}/{filename}"
                )
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
                deduplicated = str(row["status"]) == "ready"
                if not deduplicated:
                    # #1267 codex R4/R5 P1: a retry moves the row to a
                    # per-generation key, binding the PUT capability to the
                    # upload generation — every URL issued for earlier
                    # generations (and every legacy key written before the
                    # #1119 validation, P2) points at a dead key; leftover
                    # objects are orphaned to the bucket lifecycle rule. The
                    # new key is derived INSIDE the locked UPDATE from the
                    # row's own increment (R5 P1-a): a concurrent retry blocked
                    # on the row lock re-evaluates against the newest committed
                    # generation, so two racing retries can never precompute
                    # the same g{n} key from a stale snapshot. A complete()
                    # racing this is serialized by its locked identity recheck
                    # (generation included, R3 P1).
                    # row 非空 ⇒ 经 content_hash 命中（select 或 on-conflict 回落），
                    # key 前缀必为 hash 段。
                    new_row = conn.execute(
                        """
                        update materials
                        set filename=%s, content_type=%s, size_bytes=%s,
                          status='uploading', upload_generation = upload_generation + 1,
                          storage_key = %s || '/v2g' || (upload_generation + 1)
                            || '/m_' || %s || '/' || %s
                        where id=%s
                        returning storage_key
                        """,
                        (
                            filename,
                            content_type,
                            size_bytes,
                            f"{workspace_id}/{content_hash}",
                            material_id,
                            filename,
                            material_id,
                        ),
                    ).fetchone()
                    if new_row is None:
                        # 无锁 SELECT 与 UPDATE 之间行被并发 delete/TTL 回收：
                        # 对方持行锁删行提交后本 UPDATE 命中 0 行（#1269）。
                        raise NotFoundError(f"Material not found: {material_id}")
                    storage_key = str(new_row["storage_key"])
        upload_url: str | None = None
        if not deduplicated:
            upload_url = storage.presign_put(
                storage_key, size_bytes, _PRESIGN_EXPIRY_SECONDS, if_none_match=IF_NONE_MATCH_STAR
            )
        return {
            "material": _record(self._fetch_row(workspace_id, material_id)),
            "upload_url": upload_url,
            "upload_expires_in_seconds": _PRESIGN_EXPIRY_SECONDS,
            "deduplicated": deduplicated,
        }

    def complete(
        self, workspace_id: str, material_id: str, *, expected_upload_generation: int | None = None
    ) -> dict[str, Any]:
        """Verify the stored object against the declaration and mark ready.

        Verification failures flip the row to failed so the client can retry
        the upload (presign resets the row to uploading and bumps the upload
        generation). Verification runs outside any transaction (object I/O);
        the status write then rechecks the verified identity (storage_key +
        size_bytes + upload_generation) and the live status under a row lock
        (jobs/queries/material_locked_rereads) so a concurrent presign retry
        — even a same-key same-size one — can never let a stale snapshot mark
        unverified bytes ready, and a concurrent ready can never be flipped
        back by this call's own failure verdict (#1267 codex R2/R3 P1). An
        identity mismatch raises 409 and the client simply calls complete
        again (re-read, re-verify). On success the verifying HEAD's ETag is
        recorded atomically with the ready flip (materials.object_etag, v98,
        #1279): for hash-less rows it is the only content identity, and both
        consumption sides compare against it to detect a same-size overwrite
        that lands inside the issued URL's lifetime.

        ``expected_upload_generation`` (#1269 格子②): the generation the
        caller's PUT URL was issued for (from the presign response). A
        mismatch means a newer presign retry has superseded this upload —
        raise 409 immediately WITHOUT verifying or touching the row, so a
        stale client neither pays the object-I/O cost nor flips the newer
        attempt's row to failed when its own (dead-key) object is missing.
        The generation check runs BEFORE the ready idempotent fast path
        (#1284): a late complete from a superseded generation gets 409 even
        when the newer generation has already marked the row ready — only a
        same-generation replay of a ready row returns 200 idempotently.
        """
        storage = self._require_storage()
        row = self._fetch_row(workspace_id, material_id)
        generation = int(row["upload_generation"])
        if expected_upload_generation is not None and generation != expected_upload_generation:
            raise ConflictError(
                f"Material {material_id} upload superseded by generation {generation}"
                f" (expected {expected_upload_generation}); presign again to restart"
            )
        if row["status"] == "ready":
            return _record(row)
        if row["status"] == "expired":
            raise ConflictError(f"Material is expired: {material_id}")
        failure, object_etag = self._verify_object(storage, row)
        with write_transaction(self._connect_source) as conn:
            locked_status = assert_material_upload_identity_tx(conn, material_id, row)
            if locked_status == "ready":
                # 并发 complete 已按同一身份落 ready：幂等，本方结论不覆盖。
                pass
            elif locked_status == "expired":
                raise ConflictError(f"Material is expired: {material_id}")
            elif failure is not None:
                conn.execute("update materials set status='failed' where id=%s", (material_id,))
            else:
                # TTL (design §10): expires_at from the instance setting,
                # read fresh at every completion. The verified HEAD's ETag
                # lands in the same transaction (#1279): the row's content
                # identity and its ready status are atomic, so a same-size
                # overwrite after this commit is detectable by every
                # consumption side comparing against object_etag.
                mark_ready(conn, self._connect_source, material_id, object_etag=object_etag)
        if failure is not None and locked_status != "ready":
            raise MaterialVerificationError(failure)
        return _record(self._fetch_row(workspace_id, material_id))

    def _verify_object(self, storage: ObjectStorage, row: dict[str, Any]) -> tuple[str | None, str]:
        """(Failure detail or None, object ETag); logic lives in
        material_key_validation (file-budget split) — this method stays as
        the tests' injection seam. An If-Match 412 (#1279 codex P1: object
        swapped between the HEAD and the hash read by a reused PUT URL) is a
        verification failure — the row flips to failed for a client retry,
        never ready with a stale ETag."""
        try:
            return verify_stored_object(storage, row)
        except ClientError as exc:
            if not is_precondition_failed(exc):
                raise
            return f"Uploaded object changed during verification of material {row['id']}", ""

    def request_material_reupload(self, workspace_id: str, material_id: str) -> dict[str, Any]:
        """ETag 失配（消费侧 412）ready 行的受控修复 + 重传 URL（#1279
        codex r4/r5，运维入口 scripts/request_material_reupload.py）。

        编排：queries 层行锁内翻转（ready → uploading、清 etag、代次 +1、
        轮换 v2g key）→ 为**原行**的新 key 签发受代次保护的 presigned PUT
        URL——hash 与无 hash 行统一由原 material_id 愈合（无 hash 行没有
        presign 按 hash 找旧行的回路，历史 job/bundle 引用都指着原 ID）。
        客户端用该 URL PUT 后 complete(expected_upload_generation=轮换后
        代次) 即恢复 ready。uploading/failed 行幂等重签（修复重试）；
        expired → 409；未知 → 404。修复签发的 URL 与后续 presign 重试
        互斥：presign 会再次推进代次使该 URL 指向死 key（complete 代次
        守卫兜住，409 后可重新 presign 或重跑修复）。修复 URL 与 presign URL
        同样签入 If-None-Match: *（#1321）：重传方的 PUT 必须携带该头。
        """
        storage = self._require_storage()
        with write_transaction(self._connect_source) as conn:
            outcome = repair_material_for_reupload_tx(conn, workspace_id, material_id)
        key, size = str(outcome["storage_key"]), int(outcome["size_bytes"])
        outcome["upload_url"] = storage.presign_put(
            key, size, _PRESIGN_EXPIRY_SECONDS, if_none_match=IF_NONE_MATCH_STAR
        )
        return outcome

    def list(self, workspace_id: str, *, limit: int, offset: int) -> dict[str, Any]:
        limit = max(1, min(limit or _DEFAULT_LIST_LIMIT, _MAX_LIST_LIMIT))
        offset = max(0, offset)
        with read_connection(self._connect_source) as conn:
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
        with write_transaction(self._connect_source) as conn:
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
            # A bundle member cannot be deleted either (#156): the bundle
            # manifest would silently lose a file. Delete the bundle first.
            member_of = conn.execute(
                "select bundle_id from material_bundle_members where material_id=%s limit 1",
                (material_id,),
            ).fetchone()
            if member_of is not None:
                raise MaterialInUseError(f"Material is a member of bundle {member_of['bundle_id']}")
            # 先删对象再删行（保持既有顺序）：对象删除失败则事务回滚、行仍在
            # 可重试；行已删而对象残留才是不可恢复方向。
            storage.delete_object(str(row["storage_key"]))
            conn.execute("delete from materials where id=%s", (material_id,))
