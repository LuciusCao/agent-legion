"""Guarded apply-phase tail for the Worker-direct promote (#645 P2-a).

Split out of ``remote_artifact_promote.py`` for the file-size budget: the
promote module keeps the copy/backup orchestration, this module owns the
transactional tail (guarded manifest registration) plus the two mechanics it
shares with the copy phase — job-dir file promotion and authority-key
rollback restore.

EXEC-GENERATION-001: promote's authority-key copies run OUTSIDE the lock
(byte operations, reversible via the rollback backups), but the authoritative
part — job-dir file promotion and the manifest row upserts — commits in ONE
transaction that first takes the ``job-mutation:<job_id>`` advisory lock and
re-checks the lease epoch (``lease_artifact_write_current``). Because the
mutation side (rerun / upgrade / sweep teardown) holds the same lock,
exactly one ordering is possible: the registration commits first and the
mutation then removes those rows as part of its own reset, or the mutation
commits first and the gate rejects the registration — a stale promote can
neither resurrect removed manifest rows nor write bytes into the job dir.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from server.app.db.transaction import write_transaction
from server.app.executors._lease_write_gate import lease_artifact_write_current
from server.app.services.job_artifact_objects import _UPSERT_ROW_SQL, JobArtifactObjectStore
from server.app.services.job_artifact_rows import upsert_artifact_row_tx
from server.app.storage import ObjectStorage

logger = logging.getLogger(__name__)


def promote_staged_files(staged: dict[str, Path], job_dir: Path) -> None:
    """Atomically move the verified downloads into the job dir."""
    for name, staged_path in staged.items():
        target = job_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_path, target)


def restore_authority_backups(
    storage: ObjectStorage,
    promoted: list[str],
    backups: dict[str, str],
    authority_keys: dict[str, str],
) -> None:
    """Re-overwrite already-promoted authority keys from their rollback backups.

    Shared by the mid-batch copy failure path and the stale write-gate path.
    Per-key best-effort: a failed restore logs a warning and the next key is
    still attempted; keys without a backup had no prior object (the orphan is
    lifecycle's backstop)."""
    for name in promoted:
        rollback_key = backups.get(name)
        if rollback_key is None:
            continue
        try:
            storage.copy_object(rollback_key, authority_keys[name])
        except Exception:
            # #204 broad-except audit: best-effort per-key restore inside the
            # compensation path — the outcome space is the storage layer
            # (botocore surface), and per-key containment is the point: the
            # warning names the key that still holds new bytes while its
            # manifest row points at old bytes (the mismatch the backup exists
            # to prevent), the remaining keys are still attempted, and the
            # traceback rides the warning (exc_info).
            logger.warning(
                "failed to roll back artifact object %s", authority_keys[name], exc_info=True
            )


def register_remote_rows_guarded(
    object_store: JobArtifactObjectStore,
    rows: list[dict[str, Any]],
    *,
    job_id: str,
    lease_id: str,
    staged: dict[str, Path],
    job_dir: Path,
) -> bool:
    """锁内复查 + 锁内落盘 + 清单行登记，一个事务；False = 闸关（零写入）。

    复查通过后先在锁内把 staged 文件 os.replace 进 job_dir、再 upsert 清单
    行——复查与登记之间没有 reset 可介入的窗口。闸关时落盘与登记都不发
    生，调用方负责用备份恢复已完成的 authority copy 并按拒绝语义收尾。
    """
    with write_transaction(object_store.database_dsn) as conn:
        if not lease_artifact_write_current(conn, lease_id, job_id):
            return False
        promote_staged_files(staged, job_dir)
        for row in rows:
            upsert_artifact_row_tx(
                conn,
                _UPSERT_ROW_SQL,
                job_id=str(row["job_id"]),
                node_key=str(row["node_key"]),
                name=str(row["name"]),
                storage_key=str(row["storage_key"]),
                size_bytes=int(row["size_bytes"]),
                content_hash=str(row.get("content_hash") or ""),
            )
    return True
