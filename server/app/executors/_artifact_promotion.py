"""EXEC-GENERATION-001 产物字节面的共享 promote primitive（#759 复审 P1-B）。

本地上传（``JobArtifactObjectStore.upload`` 的 lease 臂）与 Worker 回传 promote
（``agent_broker.remote_artifact_promote.promote_all``）共用同一套
「备份 → copy → 锁内闸 + 登记 → 失败恢复」序列，杜绝两套相似实现：

1. 字节一律先落在 per-execution/lease 的 staging key（``artifact_staging_key``
   布局，lease_id 即本地上传的 execution 维度），绝不直写 authority key——
   入口闸通过后才提交的 reset 因此不会被旧代次字节抢跑污染 authority 对象。
2. 已存在的 authority 对象先 server-side copy 到回滚 key（零字节下载），再锁外
   copy staging→authority（可中断的大字节量不该持锁；锁外可回滚）。
3. 权威部分在**一个事务**内完成：取 ``job-mutation:<job_id>`` advisory 锁、
   ``lease_artifact_write_current`` 复查代次、（远端臂）staged 文件落盘、
   upsert 清单行。mutation 侧（rerun/upgrade/sweep teardown）持同一把锁，所以
   只有两种序：登记先提交（随后被 reset 当作重置面删除），或 reset 先提交
   （闸拒登记）——迟到 promote 既不复活已删清单行，也不污染保留行指向的字节。
4. 闸拒或 copy 中途失败时用回滚备份恢复已覆盖的 authority key；无备份说明此前
   无对象，残留是孤儿，由 bucket lifecycle 兜底。回滚 key 在任何结局都清理。

锁外 copy 的残余面论证见 docs/architecture/execution-generation.md §5。
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from server.app.db.dialect import ConnectSource
from server.app.db.transaction import write_transaction
from server.app.executors._file_promotion import FilePromotionGuard, promote_file_moves_guarded
from server.app.executors._lease_write_gate import lease_artifact_write_current
from server.app.services.job_artifact_rows import upsert_artifact_row_tx
from server.app.storage import ObjectStorage

logger = logging.getLogger(__name__)

_UPLOAD_ATTEMPTS = 3
_UPLOAD_BACKOFF_SECONDS = 0.5

# 清单行 upsert 行形的单一事实来源（两端 promote 与 legacy 直写共用；
# 自 services.job_artifact_objects 迁入本模块，靠近唯一使用它的锁内登记）。
ARTIFACT_ROW_UPSERT_SQL = """
insert into job_artifacts(
  job_id, node_key, name, storage_key, size_bytes, content_hash
) values (%s, %s, %s, %s, %s, %s)
on conflict (job_id, node_key, name) do update
set storage_key=excluded.storage_key,
    size_bytes=excluded.size_bytes,
    content_hash=excluded.content_hash,
    uploaded_at=current_timestamp
returning *
"""


@dataclass(frozen=True)
class AuthorityCopy:
    """一条 staging→authority 字节提升；rollback_key 是既有对象的备份落点。"""

    name: str
    staging_key: str
    authority_key: str
    rollback_key: str


def hash_local_file(local_path: Path) -> tuple[int, str]:
    """(size_bytes, sha256 hex) of a local artifact file."""
    size_bytes = local_path.stat().st_size
    with local_path.open("rb") as handle:
        return size_bytes, hashlib.file_digest(handle, "sha256").hexdigest()


def put_stream_with_retries(
    storage: ObjectStorage,
    storage_key: str,
    local_path: Path,
    size_bytes: int,
    *,
    job_id: str,
    name: str,
) -> None:
    """Bounded-retry streaming put; re-raises the last error after the final
    attempt for the caller's best-effort wrapper to contain."""
    last_error: Exception | None = None
    for attempt in range(_UPLOAD_ATTEMPTS):
        try:
            with local_path.open("rb") as stream:
                storage.put_stream(storage_key, stream, size_bytes)
            return
        except Exception as exc:  # storage outage must not fail the node
            # #204 broad-except audit: retry loop over the boto3 data
            # plane. put_stream's outcome space is genuinely mixed —
            # transport errors (ClientError/BotoCoreError), connection
            # resets (OSError), and the injected test fakes' own exception
            # types all take the same bounded-retry path, and the final
            # attempt re-raises for the completion hooks to contain. A
            # narrow family cannot enumerate the storage layer here
            # without also changing the public seam the tests inject.
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
    assert last_error is not None
    raise last_error


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


def discard_object(storage: ObjectStorage, storage_key: str) -> None:
    """Best-effort staging/rollback-object cleanup after promotion."""
    try:
        storage.delete_object(storage_key)
    except Exception:
        # #204 broad-except audit: deliberate best-effort staging cleanup.
        # This runs in the promote success path AND in the finally after a
        # failure — either way the caller's outcome must not change: an
        # orphaned staging object is explicitly lifecycle's backstop
        # (documented across this module family), so a storage error during
        # its deletion is only worth a warning with the traceback. The
        # storage layer is third-party surface (botocore); no business
        # exception family could enumerate it.
        logger.warning("failed to delete staging object %s", storage_key, exc_info=True)


def register_rows_guarded(
    database_dsn: ConnectSource,
    rows: list[dict[str, Any]],
    *,
    job_id: str,
    lease_id: str,
    staged_files: dict[str, Path] | None = None,
    job_dir: Path | None = None,
) -> list[dict[str, Any]] | None:
    """锁内复查 + 锁内落盘 + 清单行登记，一个事务；None = 闸关（零写入）。

    复查通过后先在锁内把 staged 文件 os.replace 进 job_dir（已存在的目标
    先备份）、再 upsert 清单行——复查与登记之间没有 reset 可介入的窗口。
    闸关时落盘与登记都不发生，调用方负责用备份恢复已完成的 authority
    copy 并按拒绝语义收尾。登记异常（upsert 失败）时已落盘的文件经
    FilePromotionGuard 整体回滚、事务回滚清单行，异常原样上抛——调用方
    随后恢复 authority copy（#759 复审 P1-2）。
    """
    with write_transaction(database_dsn) as conn:
        if not lease_artifact_write_current(conn, lease_id, job_id):
            return None
        guard = FilePromotionGuard()
        if staged_files:
            assert job_dir is not None
            guard = promote_file_moves_guarded(
                [(job_dir / name, staged_path) for name, staged_path in staged_files.items()],
                backup_parent=job_dir,
            )
        try:
            registered = [
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
        except Exception:
            # #204 broad-except audit: compensate-then-bare-re-raise (#233
            # pattern). The upsert loop's outcome space is the psycopg/DB
            # surface (constraint violation, dropped connection) plus
            # programming errors; the file promotion that already landed
            # inside this transaction is not transactional, so every flavor
            # must roll it back via the guard before the exception propagates
            # (the caller then restores the authority copies). The bare raise
            # preserves the original type; nothing is converted or masked.
            guard.rollback()
            raise
        guard.discard()
        return registered


def promote_to_authority_guarded(
    storage: ObjectStorage,
    database_dsn: ConnectSource,
    *,
    job_id: str,
    lease_id: str,
    copies: list[AuthorityCopy],
    rows: list[dict[str, Any]],
    staged_files: dict[str, Path] | None = None,
    job_dir: Path | None = None,
) -> list[dict[str, Any]] | None:
    """备份 → 锁外 copy → 锁内闸 + 登记 → 失败恢复 → 清理备份（两端共用）。

    ``rows[i]`` 登记 ``copies[i]`` 的 authority key（调用方按同一批产物构
    造）。返回登记的清单行；None = 锁内闸拒——已 copy 的 authority key 已
    按回滚备份恢复、staged 文件未落盘、清单行未登记，残留 staging/无备份
    的 authority 新对象是孤儿，lifecycle 兜底。copy 中途失败同样先恢复再
    原样上抛。登记阶段抛异常（upsert/落盘失败）也先恢复 authority copy
    再上抛——登记事务已回滚清单行、文件提升已整体反向回滚，authority
    对象不恢复就会让幸存旧清单行指向 hash/size 不匹配的新字节（#759 复审
    P1-2）。回滚备份在任何结局都清理。
    """
    authority_keys = {spec.name: spec.authority_key for spec in copies}
    backups: dict[str, str] = {}  # name -> rollback key of the pre-existing object
    for spec in copies:
        if storage.head_object(spec.authority_key) is not None:
            storage.copy_object(spec.authority_key, spec.rollback_key)
            backups[spec.name] = spec.rollback_key
    promoted: list[str] = []
    try:
        try:
            for spec in copies:
                storage.copy_object(spec.staging_key, spec.authority_key)
                promoted.append(spec.name)
        except Exception:
            # #204 broad-except audit: compensate-then-bare-re-raise (#233
            # pattern). The batch loop's outcome space is mixed — storage-layer
            # errors (botocore surface), ValueError from Worker-untrusted refs,
            # and programming errors all must roll back the already-overwritten
            # authority keys before propagating; the re-raise preserves the
            # original type for the caller's classification, so nothing is
            # converted or masked.
            restore_authority_backups(storage, promoted, backups, authority_keys)
            raise
        try:
            registered = register_rows_guarded(
                database_dsn,
                rows,
                job_id=job_id,
                lease_id=lease_id,
                staged_files=staged_files,
                job_dir=job_dir,
            )
        except Exception:
            # #204 broad-except audit: same compensate-then-bare-re-raise
            # pattern as the copy loop above — the registration surface mixes
            # storage-layer file errors (the locked promote's os.replace) and
            # the psycopg/DB family (upsert failure, dropped connection), and
            # every flavor leaves overwritten authority keys whose manifest
            # rows just rolled back; restoring from the rollback backups is
            # unconditional, the original exception type rides through for
            # the caller's classification.
            restore_authority_backups(storage, promoted, backups, authority_keys)
            raise
        if registered is None:
            # 锁内闸拒：reset 落在入口预检与登记之间。用备份恢复 authority
            # copy，让幸存（或缺失）的旧清单行仍指向匹配的旧字节。
            restore_authority_backups(storage, promoted, backups, authority_keys)
    finally:
        for rollback_key in backups.values():
            discard_object(storage, rollback_key)
    return registered


def upload_via_staging_guarded(
    storage: ObjectStorage,
    database_dsn: ConnectSource,
    *,
    job_id: str,
    lease_id: str,
    name: str,
    local_path: Path,
    size_bytes: int,
    staging_key: str,
    authority_key: str,
    rollback_key: str,
    row: dict[str, Any],
) -> dict[str, Any] | None:
    """本地 lease 臂上传：字节先落 per-lease staging key，再走共享 primitive。

    单产物版的 promote：调用方（``JobArtifactObjectStore.upload``）推导
    staging/authority/rollback key（lease_id 即 execution 维度）与清单行。
    闸拒返回 None——authority 对象已按回滚备份恢复，保留的旧清单行仍指向
    匹配的旧字节；staging key 在任何结局都清理（本地臂自建自删；远端臂的
    Worker staging 失败时留给 lifecycle，语义不同）。
    """
    copy_spec = AuthorityCopy(
        name=name,
        staging_key=staging_key,
        authority_key=authority_key,
        rollback_key=rollback_key,
    )
    try:
        put_stream_with_retries(
            storage, staging_key, local_path, size_bytes, job_id=job_id, name=name
        )
        registered = promote_to_authority_guarded(
            storage,
            database_dsn,
            job_id=job_id,
            lease_id=lease_id,
            copies=[copy_spec],
            rows=[row],
        )
    finally:
        discard_object(storage, staging_key)
    if registered is None:
        logger.info(
            "artifact upload discarded (stale lease %s, job %s, %s)", lease_id, job_id, name
        )
        return None
    return registered[0]
