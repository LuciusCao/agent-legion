"""Content-addressed artifact store with reference counting baseline."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path

from server.app.db.transaction import read_connection, write_transaction
from server.app.storage_paths import resolve_managed_path

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class ArtifactStoreError(Exception):
    """Base class for artifact store failures."""


class ArtifactNotFoundError(ArtifactStoreError):
    """Raised when an artifact hash is malformed or unknown."""


class ArtifactStore:
    def __init__(self, root: Path, db_path: str) -> None:
        self.root = root
        self.db_path = db_path
        (self.root / ".staging").mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        final = self.root / digest[:2] / digest
        if final.exists():
            return digest
        staging = self.root / ".staging" / str(uuid.uuid4())
        try:
            staging.write_bytes(data)
            with staging.open("rb") as fh:
                os.fsync(fh.fileno())
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, final)  # 原子发布，崩溃不留半成品
        finally:
            staging.unlink(missing_ok=True)
        with write_transaction(self.db_path) as conn:
            conn.execute(
                "insert into artifacts(hash, size) values (?, ?) on conflict(hash) do nothing",
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
                "insert into artifact_refs(job_id, node_key, name, hash) values (?, ?, ?, ?)"
                " on conflict(job_id, node_key, name) do update set hash=excluded.hash",
                (job_id, node_key, name, hash),
            )

    def refs_for_job(self, job_id: str) -> list[dict]:
        with read_connection(self.db_path) as conn:
            rows = conn.execute(
                "select job_id, node_key, name, hash from artifact_refs where job_id = ?"
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
                    "select hash from artifact_refs where job_id = ?", (job_id,)
                )
            ]
            conn.execute("delete from artifact_refs where job_id = ?", (job_id,))
            orphaned = [
                h
                for h in hashes
                if (
                    conn.execute(
                        "select count(*) as cnt from artifact_refs where hash = ?", (h,)
                    ).fetchone()
                    or {"cnt": 0}
                )["cnt"]
                == 0
            ]
        return orphaned

    def delete_unreferenced(self, hashes: list[str]) -> int:
        deleted = 0
        with write_transaction(self.db_path) as conn:
            for h in hashes:
                refs_row = conn.execute(
                    "select count(*) as cnt from artifact_refs where hash = ?", (h,)
                ).fetchone()
                refs = int(refs_row["cnt"]) if refs_row is not None else 0
                if refs:
                    continue
                conn.execute("delete from artifacts where hash = ?", (h,))
                (self.root / h[:2] / h).unlink(missing_ok=True)
                deleted += 1
        return deleted
