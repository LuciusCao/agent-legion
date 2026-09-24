"""Job artifact object storage (materials-and-runs design §6.5, D12, #160).

The authoritative copy of every declared node artifact lives in the instance
S3-compatible object store under ``jobs/{workspace_id}/{job_id}/{name}``;
``job_artifacts`` is the manifest table and the local job_dir copy is an
evictable cache (EXEC-ARTIFACT-STORE-001). Reads resolve local-first with the
object store as fallback so legacy jobs (never uploaded) keep working without
data migration. With no bucket configured the whole feature is inert: writes are
no-ops and lookups return None, so callers fall back to the local job_dir.

Object-layer gzip compression (#338): v4+ Workers upload gzip-compressed bytes
under a ``.gz``-suffixed ``storage_key``; the suffix is the form marker and
``open_stream`` decodes transparently (scheme: ``job_artifact_gzip``).
``size_bytes`` on a ``.gz`` row is the stored (compressed) size — the only
HEAD-verifiable number.

EXEC-GENERATION-001 byte plane (#759 review P1-B): the ``lease_id`` arm of
``upload`` never writes the authority key directly — bytes land on a
per-invocation staging key (unique attempt namespace per call, so concurrent
same-lease retries never share staging/rollback objects) and are promoted
through the shared
``executors._artifact_promotion.promote_to_authority_guarded`` primitive
(backup → copy → in-transaction generation recheck + manifest row → rollback
restore on rejection, the whole per-key sequence serialized by a
per-authority-key advisory lock), the same sequence the Worker-result promote
uses, so a reset landing after the entry gate cannot leave old manifest rows
pointing at polluted bytes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

from server.app.db.dialect import ConnectSource
from server.app.db.transaction import read_connection, write_transaction
from server.app.executors._artifact_promotion import (
    ARTIFACT_ROW_UPSERT_SQL,
    hash_local_file,
    put_stream_with_retries,
    upload_via_staging_guarded,
)
from server.app.executors._lease_write_gate import lease_artifact_write_current
from server.app.services.job_artifact_gzip import GZIP_SUFFIX, content_stream
from server.app.services.job_artifact_rows import upsert_artifact_row_tx
from server.app.storage import ObjectStorage

logger = logging.getLogger(__name__)

# Public: the claim-time injector derives longer presign TTLs from the node
# timeout on top of this floor (agent_broker.remote_artifact_support).
DEFAULT_PRESIGN_EXPIRY_SECONDS = 3600

# Bucket key prefix for job artifacts (materials keys live at the bucket
# root); the prefix lets bucket lifecycle rules target artifacts separately.
KEY_PREFIX = "jobs"
# Workers upload to a per-execution staging prefix; the Host promotes onto
# the authority key server-side after verification (#160, so a stale Worker's
# late PUT can never overwrite the authority copy). Lifecycle rules should
# give this prefix a short retention (orphans are the failure residue).
STAGING_KEY_PREFIX = "jobs-staging"


def artifact_storage_key(workspace_id: str, job_id: str, name: str) -> str:
    return f"{KEY_PREFIX}/{workspace_id}/{job_id}/{name}"


def artifact_staging_key(workspace_id: str, job_id: str, execution_id: str, name: str) -> str:
    return f"{STAGING_KEY_PREFIX}/{workspace_id}/{job_id}/{execution_id}/{name}"


def valid_artifact_name(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and name not in {".", ".."}


class JobArtifactObjectStore:
    """Upload/register/lookup service for job artifacts in object storage."""

    def __init__(self, database_dsn: ConnectSource, storage: ObjectStorage | None = None) -> None:
        # database_dsn: JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187).
        self._dsn = database_dsn
        # Public seam: tests inject a fake ObjectStorage; an unconfigured
        # instance keeps None and every caller falls back to the job_dir.
        self.storage = storage

    @property
    def enabled(self) -> bool:
        return self.storage is not None

    @property
    def database_dsn(self) -> ConnectSource:
        """The connection source; the guarded promote opens its serialized
        transaction on it (executors._artifact_promotion.promote_to_authority_guarded)."""
        return self._dsn

    def artifact_write_gate_open(self, *, job_id: str, lease_id: str) -> bool:
        """EXEC-GENERATION-001 产物写闸的一次性复查（#645 P2）。

        True = lease 仍是本 job 当前代次的 active lease，调用方可以继续做
        字节级上传/copy。这只是锁外快路径——重置落在复查之后时，权威的拦
        截在共享 promote primitive 的清单行登记事务里
        （``executors._artifact_promotion.register_rows_guarded``）。
        """
        with write_transaction(self._dsn) as conn:
            return lease_artifact_write_current(conn, lease_id, job_id)

    def upload(
        self,
        *,
        workspace_id: str,
        job_id: str,
        node_key: str,
        name: str,
        local_path: Path,
        lease_id: str = "",
    ) -> dict[str, Any] | None:
        """Upload one produced artifact and upsert its manifest row.

        Returns None when object storage is not configured. Raises after
        bounded retries on persistent storage errors — the completion hooks
        catch, log and continue (the local copy stays; the reconciler
        re-uploads later).

        With ``lease_id`` the write goes through the EXEC-GENERATION-001
        artifact byte plane (#759 review P1-B): bytes land on a per-invocation
        staging key first and are promoted by the shared
        ``promote_to_authority_guarded`` primitive — the manifest row
        registers only if the lease still owns the current generation inside
        the job-mutation-locked transaction, and a rejected promote restores
        the authority object from its rollback backup, so a reset landing
        between the upload loop's entry check and the row write can neither
        resurrect a removed manifest row nor leave a kept row pointing at
        polluted bytes. Returns None on such a rejection.

        Without ``lease_id`` (reconciler re-uploads, approval artifact
        promotion) the legacy direct write stays: those callers run outside
        any lease context, so there is no execution generation to CAS against
        — the write gate could never open for them, and their upsert semantics
        (refresh the manifest row to match the bytes on disk) are the desired
        reconciliation behavior.
        """
        if self.storage is None:
            return None
        if not valid_artifact_name(name):
            raise ValueError(f"invalid artifact name: {name!r}")
        size_bytes, content_hash = hash_local_file(local_path)
        storage_key = artifact_storage_key(workspace_id, job_id, name)
        if lease_id:
            # 每次调用独立 attempt 命名空间（codex #774 P1×2）：并发重试
            # （同 lease 同名、不同字节）的 staging/rollback 对象若共享
            # key，put_stream 在锁外会让后写者覆盖先写者的 staging 字节
            # （先写者把后写者字节 promote 进 authority、却登记自己的
            # size/hash），先行者的 finally 还会删掉后者的 staging/rollback
            # 对象（后者闸拒/登记失败时恢复无备份可取）。per-invocation
            # key 从构造上拆掉这两条跨调用通道。
            attempt = uuid4().hex
            return upload_via_staging_guarded(
                self.storage,
                self._dsn,
                job_id=job_id,
                lease_id=lease_id,
                name=name,
                local_path=local_path,
                size_bytes=size_bytes,
                staging_key=artifact_staging_key(
                    workspace_id, job_id, f"{lease_id}/{attempt}", name
                ),
                authority_key=storage_key,
                rollback_key=artifact_staging_key(
                    workspace_id, job_id, lease_id, f".rollback/{attempt}/{name}"
                ),
                row={
                    "job_id": job_id,
                    "node_key": node_key,
                    "name": name,
                    "storage_key": storage_key,
                    "size_bytes": size_bytes,
                    "content_hash": content_hash,
                },
            )
        put_stream_with_retries(
            self.storage, storage_key, local_path, size_bytes, job_id=job_id, name=name
        )
        return self._register_row(
            job_id=job_id,
            node_key=node_key,
            name=name,
            storage_key=storage_key,
            size_bytes=size_bytes,
            content_hash=content_hash,
        )

    def verify_remote(
        self,
        *,
        workspace_id: str,
        job_id: str,
        name: str,
        storage_key: str,
        size_bytes: int,
        execution_id: str | None = None,
        max_size_bytes: int | None = None,
    ) -> None:
        """HEAD-verify a Worker-reported object WITHOUT registering it.

        The validation half of ``record_remote``: the result-commit path
        verifies ALL reported refs before applying ANY (no half-applied
        state), then registers/downloads them in the apply phase. With
        ``execution_id`` the expected key is the per-execution staging key
        (Worker uploads); without it, the authority key. ``max_size_bytes``
        applies the same size ceiling the legacy archive channel enforces
        (instance setting ``agent_workers.max_archive_bytes``).
        """
        if self.storage is None:
            raise ValueError("object storage is not configured")
        if max_size_bytes is not None and size_bytes > max_size_bytes:
            raise ValueError(
                f"uploaded object size {size_bytes} exceeds the artifact size "
                f"limit {max_size_bytes} for {name!r}"
            )
        if execution_id:
            expected_key = artifact_staging_key(workspace_id, job_id, execution_id, name)
        else:
            expected_key = artifact_storage_key(workspace_id, job_id, name)
        # #338 dual-form: v4+ Workers upload to the ``.gz``-suffixed key,
        # older Workers to the bare key — the Host accepts both and the
        # suffix alone decides the stored form downstream.
        if storage_key not in {expected_key, expected_key + GZIP_SUFFIX}:
            raise ValueError(f"unexpected artifact storage key: {storage_key!r}")
        head = self.storage.head_object(storage_key)
        if head is None:
            raise ValueError(f"uploaded object is missing: {name!r}")
        if head.size_bytes != size_bytes:
            raise ValueError(
                f"uploaded object size {head.size_bytes} does not match "
                f"the declared size {size_bytes} for {name!r}"
            )

    def record_remote(
        self,
        *,
        workspace_id: str,
        job_id: str,
        node_key: str,
        name: str,
        storage_key: str,
        size_bytes: int,
        content_hash: str = "",
    ) -> dict[str, Any] | None:
        """Register an artifact a Worker uploaded directly (HEAD-verified).

        The key must match the server-side layout and the stored object must
        exist with the declared size — Worker reports are untrusted.
        """
        if self.storage is None:
            return None
        self.verify_remote(
            workspace_id=workspace_id,
            job_id=job_id,
            name=name,
            storage_key=storage_key,
            size_bytes=size_bytes,
        )
        return self._register_row(
            job_id=job_id,
            node_key=node_key,
            name=name,
            storage_key=storage_key,
            size_bytes=size_bytes,
            content_hash=content_hash,
        )

    def record_remote_many(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """Batch variant of ``record_remote``: ONE write transaction.

        The promote phase of the Worker-direct channel registers every
        already-verified ref atomically — a mid-batch failure rolls the whole
        batch back instead of leaving a half-registered manifest (no
        half-applied outputs). Callers verify all refs first; each row
        carries workspace_id/job_id/node_key/name/storage_key/size_bytes/
        content_hash.
        """
        if self.storage is None:
            return None
        with write_transaction(self._dsn) as conn:
            return [
                upsert_artifact_row_tx(
                    conn,
                    ARTIFACT_ROW_UPSERT_SQL,
                    job_id=str(row["job_id"]),
                    node_key=str(row["node_key"]),
                    name=str(row["name"]),
                    storage_key=str(row["storage_key"]),
                    size_bytes=int(row["size_bytes"]),
                    content_hash=str(row.get("content_hash") or ""),
                )
                for row in rows
            ]

    def _register_row(
        self,
        *,
        job_id: str,
        node_key: str,
        name: str,
        storage_key: str,
        size_bytes: int,
        content_hash: str,
    ) -> dict[str, Any] | None:
        """Single-row upsert in its own transaction (batch path inlines it).

        Ungated by design: the only callers left are the no-lease paths
        (``record_remote`` HEAD-verified registrations, the reconciler and
        approval direct writes) — lease-carrying writes go through
        ``register_rows_guarded`` inside the shared promote primitive.
        """
        with write_transaction(self._dsn) as conn:
            return upsert_artifact_row_tx(
                conn,
                ARTIFACT_ROW_UPSERT_SQL,
                job_id=job_id,
                node_key=node_key,
                name=name,
                storage_key=storage_key,
                size_bytes=size_bytes,
                content_hash=content_hash,
            )

    def lookup(self, job_id: str, name: str) -> dict[str, Any] | None:
        """Latest manifest row for an artifact name (internal: has storage_key)."""
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                "select * from job_artifacts where job_id=%s and name=%s"
                " order by uploaded_at desc limit 1",
                (job_id, name),
            ).fetchone()
        return dict(row) if row is not None else None

    def row_for_node(self, job_id: str, node_key: str, name: str) -> dict[str, Any] | None:
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                "select * from job_artifacts where job_id=%s and node_key=%s and name=%s",
                (job_id, node_key, name),
            ).fetchone()
        return dict(row) if row is not None else None

    def rows_for_job(self, job_id: str) -> list[dict[str, Any]]:
        with read_connection(self._dsn) as conn:
            rows = conn.execute(
                "select * from job_artifacts where job_id=%s order by uploaded_at",
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def names_for_job(self, job_id: str) -> set[str]:
        with read_connection(self._dsn) as conn:
            rows = conn.execute(
                "select distinct name from job_artifacts where job_id=%s",
                (job_id,),
            ).fetchall()
        return {str(row["name"]) for row in rows}

    def live_keys_for(self, job_id: str, storage_keys: list[str]) -> set[str]:
        """Targeted existence probe: which of ``storage_keys`` the job's
        CURRENT manifest registers (#706 review P2).

        The rerun cleanup's re-attempt guard needs per-key liveness, not the
        manifest's contents — a full ``rows_for_job`` read per retired object
        degraded multi-artifact reruns to O(retired x manifest rows) on the
        sync request path. This probe ships only the queried keys both ways
        (the job_id predicate rides the manifest PK prefix). Like
        ``existing_object_storage_keys`` (#344) it is a read-then-act TOCTOU
        guard, not an atomic conditional removal: a caller acting on the
        answer must tolerate — and diagnose — a re-registration landing in
        the probe-to-act gap.
        """
        if not storage_keys:
            return set()
        with read_connection(self._dsn) as conn:
            rows = conn.execute(
                "select storage_key from job_artifacts where job_id=%s and storage_key = ANY(%s)",
                (job_id, storage_keys),
            ).fetchall()
        return {str(row["storage_key"]) for row in rows}

    def open_stream(self, row: dict[str, Any]) -> BinaryIO:
        """Content-byte stream: ``.gz`` objects decode transparently (#338);
        use ``open_object_stream`` for the stored bytes as-is."""
        assert self.storage is not None
        return content_stream(self.storage, str(row["storage_key"]))

    def open_object_stream(self, row: dict[str, Any]) -> BinaryIO:
        """Stored bytes as-is: raw-endpoint gzip passthrough / HEAD semantics."""
        assert self.storage is not None
        return self.storage.open_stream(str(row["storage_key"]))

    def open_range_stream(self, row: dict[str, Any], start: int, end: int) -> BinaryIO:
        """Ranged read [start, end] inclusive; undefined for ``.gz`` (#338)."""
        assert self.storage is not None
        return self.storage.open_range(str(row["storage_key"]), start, end)

    def delete_objects(self, rows: list[dict[str, Any]]) -> None:
        """Best-effort object deletion for manifest rows snapshot before a
        job deletion (the rows themselves cascade away with the job row, so
        object cleanup happens after commit — mirroring the local trash /
        artifact-blob GC ordering; bucket lifecycle is the orphan backstop).
        """
        if self.storage is None:
            return
        for row in rows:
            try:
                self.storage.delete_object(str(row["storage_key"]))
            except Exception:
                # #204 broad-except audit: per-object best-effort after the
                # job row already committed — the outcome space spans the boto3
                # data plane (ClientError/BotoCoreError), transport resets
                # (OSError), and the test-injected fakes' exception types; no
                # narrow business family can enumerate the storage layer. A
                # failed delete leaves an orphan the bucket lifecycle rule
                # reaps (deployment doc), which is strictly better than
                # failing an already-committed deletion. The traceback is
                # logged so the residue is diagnosable.
                logger.warning(
                    "artifact object removal failed for %s", row["storage_key"], exc_info=True
                )
