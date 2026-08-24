"""Job artifact object storage (materials-and-runs design §6.5, D12, #160).

The authoritative copy of every declared node artifact lives in the instance
S3-compatible object store under ``jobs/{workspace_id}/{job_id}/{name}``;
``job_artifacts`` is the manifest table and the local job_dir copy is an
evictable cache (EXEC-ARTIFACT-STORE-001). Reads resolve object-storage-first with
a local fallback so legacy jobs (never uploaded) keep working without data
migration. With no bucket configured the whole feature is inert: writes are
no-ops and lookups return None, so callers fall back to the local job_dir.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Any, BinaryIO

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction
from server.app.storage import ObjectStorage

logger = logging.getLogger(__name__)

_HASH_CHUNK_BYTES = 1024 * 1024
_UPLOAD_ATTEMPTS = 3
_UPLOAD_BACKOFF_SECONDS = 0.5
_DEFAULT_PRESIGN_EXPIRY_SECONDS = 3600

# Bucket key prefix for job artifacts (materials keys live at the bucket
# root); the prefix lets bucket lifecycle rules target artifacts separately.
KEY_PREFIX = "jobs"


def artifact_storage_key(workspace_id: str, job_id: str, name: str) -> str:
    return f"{KEY_PREFIX}/{workspace_id}/{job_id}/{name}"


def valid_artifact_name(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and name not in {".", ".."}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


class JobArtifactObjectStore:
    """Upload/register/lookup service for job artifacts in object storage."""

    def __init__(self, database_dsn: DatabaseDsn, storage: ObjectStorage | None = None) -> None:
        self._dsn = database_dsn
        # Public seam: tests inject a fake ObjectStorage; an unconfigured
        # instance keeps None and every caller falls back to the job_dir.
        self.storage = storage

    @property
    def enabled(self) -> bool:
        return self.storage is not None

    def upload(
        self,
        *,
        workspace_id: str,
        job_id: str,
        node_key: str,
        name: str,
        local_path: Path,
    ) -> dict[str, Any] | None:
        """Upload one produced artifact and upsert its manifest row.

        Returns None when object storage is not configured. Raises after
        bounded retries on persistent storage errors — the completion hooks
        catch, log and continue (the local copy stays; the reconciler
        re-uploads later).
        """
        if self.storage is None:
            return None
        if not valid_artifact_name(name):
            raise ValueError(f"invalid artifact name: {name!r}")
        size_bytes = local_path.stat().st_size
        content_hash = _sha256(local_path)
        storage_key = artifact_storage_key(workspace_id, job_id, name)
        last_error: Exception | None = None
        for attempt in range(_UPLOAD_ATTEMPTS):
            try:
                with local_path.open("rb") as stream:
                    self.storage.put_stream(storage_key, stream, size_bytes)
                break
            except Exception as exc:  # storage outage must not fail the node
                last_error = exc
                logger.warning(
                    "artifact upload attempt %d/%d failed for job %s %s: %s",
                    attempt + 1,
                    _UPLOAD_ATTEMPTS,
                    job_id,
                    name,
                    exc,
                )
                if attempt + 1 < _UPLOAD_ATTEMPTS:
                    time.sleep(_UPLOAD_BACKOFF_SECONDS * (2**attempt))
        else:
            assert last_error is not None
            raise last_error
        return self._upsert_row(
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
    ) -> None:
        """HEAD-verify a Worker-reported object WITHOUT registering it.

        The validation half of ``record_remote``: the result-commit path
        verifies ALL reported refs before applying ANY (no half-applied
        state), then registers/downloads them in the apply phase.
        """
        if self.storage is None:
            raise ValueError("object storage is not configured")
        expected_key = artifact_storage_key(workspace_id, job_id, name)
        if storage_key != expected_key:
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
        return self._upsert_row(
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

    def open_stream(self, row: dict[str, Any]) -> BinaryIO:
        assert self.storage is not None
        return self.storage.open_stream(str(row["storage_key"]))

    def presign_get(
        self, row: dict[str, Any], expires_seconds: int = _DEFAULT_PRESIGN_EXPIRY_SECONDS
    ) -> str:
        """Presigned GET for Worker input staging (claim path, memory only)."""
        assert self.storage is not None
        return self.storage.presign_get(str(row["storage_key"]), expires_seconds)

    def presign_put(
        self,
        *,
        workspace_id: str,
        job_id: str,
        name: str,
        size_bytes: int = 0,
        expires_seconds: int = _DEFAULT_PRESIGN_EXPIRY_SECONDS,
    ) -> tuple[str, str]:
        """(storage_key, presigned PUT URL) for direct Worker upload."""
        assert self.storage is not None
        storage_key = artifact_storage_key(workspace_id, job_id, name)
        url = self.storage.presign_put(storage_key, size_bytes, expires_seconds)
        return storage_key, url

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
                logger.warning(
                    "failed to delete artifact object %s", row["storage_key"], exc_info=True
                )

    def _upsert_row(
        self,
        *,
        job_id: str,
        node_key: str,
        name: str,
        storage_key: str,
        size_bytes: int,
        content_hash: str,
    ) -> dict[str, Any]:
        with write_transaction(self._dsn) as conn:
            row = conn.execute(
                """
                insert into job_artifacts(
                  job_id, node_key, name, storage_key, size_bytes, content_hash
                ) values (%s, %s, %s, %s, %s, %s)
                on conflict (job_id, node_key, name) do update
                set storage_key=excluded.storage_key,
                    size_bytes=excluded.size_bytes,
                    content_hash=excluded.content_hash,
                    uploaded_at=current_timestamp
                returning *
                """,
                (job_id, node_key, name, storage_key, size_bytes, content_hash),
            ).fetchone()
        assert row is not None
        return dict(row)
