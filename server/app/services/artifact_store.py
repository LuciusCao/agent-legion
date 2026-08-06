"""Content-addressed artifact store with reference counting baseline."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from server.app.db.transaction import read_connection, write_transaction
from server.app.storage_paths import resolve_managed_path

logger = logging.getLogger(__name__)

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

# Freshly stored artifacts may still be in flight between ``put`` and
# ``add_ref`` (or between a worker upload and its result report), so GC must
# not reclaim blobs younger than this grace window.
GC_GRACE_SECONDS = 600


def _as_utc(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


class ArtifactStoreError(Exception):
    """Base class for artifact store failures."""


class ArtifactNotFoundError(ArtifactStoreError):
    """Raised when an artifact hash is malformed or unknown."""


class ArtifactStore:
    def __init__(
        self, root: Path, db_path: str, gc_grace_seconds: float = GC_GRACE_SECONDS
    ) -> None:
        self.root = root
        self.db_path = db_path
        self.gc_grace_seconds = gc_grace_seconds
        (self.root / ".staging").mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        final = self.root / digest[:2] / digest
        if not final.exists():
            staging = self.root / ".staging" / str(uuid.uuid4())
            try:
                staging.write_bytes(data)
                with staging.open("rb") as fh:
                    os.fsync(fh.fileno())
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging, final)  # 原子发布，崩溃不留半成品
            finally:
                staging.unlink(missing_ok=True)
        # Always (re)assert the catalog row: a crash between the file publish
        # and this insert (or a GC race) can leave a blob without a row, and
        # artifact_refs.hash has an FK to artifacts(hash).
        with write_transaction(self.db_path) as conn:
            conn.execute(
                "insert into artifacts(hash, size) values (%s, %s) on conflict(hash) do nothing",
                (digest, len(data)),
            )
        return digest

    def open(self, hash: str) -> Path:
        if not _HASH_RE.match(hash):
            raise ArtifactNotFoundError(f"malformed artifact hash: {hash!r}")
        path = resolve_managed_path(
            self.root,
            f"{hash[:2]}/{hash}",
            allow_missing=True,
            record_id=hash,
            root_kind="artifacts",
        )
        if not path.is_file():
            raise ArtifactNotFoundError(hash)
        return path

    def add_ref(self, job_id: str, node_key: str, name: str, hash: str) -> None:
        with write_transaction(self.db_path) as conn:
            conn.execute(
                "insert into artifact_refs(job_id, node_key, name, hash) values (%s, %s, %s, %s)"
                " on conflict(job_id, node_key, name) do update set hash=excluded.hash",
                (job_id, node_key, name, hash),
            )

    def refs_for_job(self, job_id: str) -> list[dict]:
        with read_connection(self.db_path) as conn:
            rows = conn.execute(
                "select job_id, node_key, name, hash from artifact_refs where job_id = %s"
                " order by node_key, name",
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_refs_for_job(self, job_id: str) -> list[str]:
        # 单事务内“先删后查孤儿”：返回删除后不再被任何 job 引用的 hash。
        with write_transaction(self.db_path) as conn:
            hashes = [
                row["hash"]
                for row in conn.execute(
                    "select hash from artifact_refs where job_id = %s", (job_id,)
                )
            ]
            conn.execute("delete from artifact_refs where job_id = %s", (job_id,))
            orphaned = [
                h
                for h in hashes
                if (
                    conn.execute(
                        "select count(*) as cnt from artifact_refs where hash = %s", (h,)
                    ).fetchone()
                    or {"cnt": 0}
                )["cnt"]
                == 0
            ]
        return orphaned

    def delete_unreferenced(self, hashes: list[str], now: datetime | None = None) -> int:
        # DB transaction only re-checks refcounts and deletes catalog rows;
        # blob files are unlinked after commit so an aborted transaction can
        # never leave refs pointing at deleted blobs.
        cutoff = (now or datetime.now(UTC)) - timedelta(seconds=self.gc_grace_seconds)
        deleted_paths: list[Path] = []
        with write_transaction(self.db_path) as conn:
            for h in hashes:
                refs_row = conn.execute(
                    "select count(*) as cnt from artifact_refs where hash = %s", (h,)
                ).fetchone()
                refs = int(refs_row["cnt"]) if refs_row is not None else 0
                if refs:
                    continue
                created_row = conn.execute(
                    "select created_at from artifacts where hash = %s", (h,)
                ).fetchone()
                if created_row is None:
                    continue
                created_at = _as_utc(created_row["created_at"])
                if created_at is None or created_at > cutoff:
                    # Too young: an upload may still be in flight between
                    # put() and add_ref(); leave it for a later sweep.
                    continue
                conn.execute("delete from artifacts where hash = %s", (h,))
                deleted_paths.append(self.root / h[:2] / h)
        for path in deleted_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # Orphan file without a catalog row: harmless, reclaimed by a
                # later pass; never fail the committed deletion.
                logger.warning("Failed to unlink artifact blob %s", path, exc_info=True)
        return len(deleted_paths)
