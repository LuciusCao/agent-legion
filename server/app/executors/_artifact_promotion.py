"""EXEC-GENERATION-001 产物字节面的共享 promote primitive（#759 复审 P1-B）。

本地上传（``JobArtifactObjectStore.upload`` 的 lease 臂）与 Worker 回传 promote
（``agent_broker.remote_artifact_promote.promote_all``）共用同一套
「备份 → copy → 锁内闸 + 登记 → 失败恢复」序列，杜绝两套相似实现：

1. 字节一律先落在 staging key（``artifact_staging_key`` 布局）——本地臂为
   每次调用生成独立 attempt 命名空间，并发同 lease 重试的 put_stream
   （锁外）互不覆盖、finally 清理互不误删（codex #774 P1）；远端臂沿用
   协议固定的 per-execution key（重试携带相同 refs、字节相同）。绝不直
   写 authority key——入口闸通过后才提交的 reset 因此不会被旧代次字节
   抢跑污染 authority 对象。
2. 同一 authority key 的并发 promote 经 ``artifact-authority:<key>`` advisory
   锁（升序、任何字节操作之前取）**全程串行**：备份、copy、闸内登记与
   失败恢复（commit 时刻失败除外，见第 4 条残余）都在同一事务的同一把
   按 key 锁内——过期 promote 的恢复在构造上不可能插进新代次 promote 的
   copy 与登记之间（#759 复审 P1-C）。大字节量的 copy 仍不持 job-mutation
   锁（mutation 侧不取 artifact 锁，不被阻塞）；copy 持有按 key 锁只让
   同 key 的 promote 排队——那本来就必须串行。
3. 权威部分在**一个事务**内完成：按 key 锁升序 → 备份/copy → 取
   ``job-mutation:<job_id>`` advisory 锁、``lease_artifact_write_current``
   复查代次、（远端臂）staged 文件落盘、upsert 清单行。mutation 侧（rerun/
   upgrade/sweep teardown）持同一把 job-mutation 锁，所以只有两种序：登记先
   提交（随后被 reset 当作重置面删除），或 reset 先提交（闸拒登记）——迟到
   promote 既不复活已删清单行，也不污染保留行指向的字节。
4. 闸拒与 copy/登记中途失败时用回滚备份恢复已覆盖的 authority key（事务
   死亡前、仍在按 key 锁内）；commit 时刻失败（连接死亡）是不可约例外：
   锁随会话释放，恢复降级为无串行 best-effort，选边偏向 commit 歧义中远
   更常见的 rollback half（docs §4）。无备份说明此前无对象，残留是孤儿，
   由 bucket lifecycle 兜底（ack 歧义下「copy 尝试过但失败」一律按「可能
   已覆盖」进恢复集，只有从未尝试的 key 才按冗余删备份，#774 对抗复审
   P1）。回滚 key 由调用方按调用唯一化（attempt 维度，codex #774 P1）
   ——锁外清理只删自己 attempt 的备份，并发重试的备份互不误删。**备份
   删除的前提是它所防范的状态已确认解除**（登记提交、恢复成功或该 key
   的 copy 从未被尝试）：恢复 copy 在锁仍持有的臂带界重试、锁已随会话
   释放的臂单发（退避只放大无锁踩新代次的窗口，#774 对抗复审）；重试
   耗尽仍失败的备份是幸存清单行所指向旧字节的最后恢复源，必须保留
   （codex #774 P1——无条件删除会把清单行与 authority 字节的错位变成
   永久不可恢复），ERROR 日志携带 authority/backup key 作为恢复指针。

锁序与残余面论证见 docs/architecture/execution-generation.md §2.8/§4。
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from server.app.db.connection import DatabaseConnection
from server.app.db.dialect import ConnectSource
from server.app.db.transaction import write_transaction
from server.app.executors._artifact_restore import (
    _STORAGE_OP_ATTEMPTS,
    _STORAGE_OP_BACKOFF_SECONDS,
    discard_object,
    restore_authority_backups,
)
from server.app.executors._file_promotion import FilePromotionGuard, promote_file_moves_guarded
from server.app.executors._lease_write_gate import lease_artifact_write_current
from server.app.services.job_artifact_rows import upsert_artifact_row_tx
from server.app.storage import ObjectStorage

logger = logging.getLogger(__name__)

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
    """一条 staging→authority 字节提升；rollback_key 是既有对象的备份落点。

    rollback_key 必须由调用方按调用唯一化（attempt 维度）：并发 promote
    同名产物的重试共享 rollback key 时，先提交者的锁外清理会删掉后者的
    备份（codex #774 P1）。"""

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
    for attempt in range(_STORAGE_OP_ATTEMPTS):
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
                _STORAGE_OP_ATTEMPTS,
                job_id,
                name,
                exc,
            )
            if attempt + 1 < _STORAGE_OP_ATTEMPTS:
                time.sleep(_STORAGE_OP_BACKOFF_SECONDS * (2**attempt))
    assert last_error is not None
    raise last_error


def _lock_authority_keys_tx(conn: DatabaseConnection, authority_keys: list[str]) -> None:
    """按 key 串行化同一 authority key 的并发 promote（#759 复审 P1-C）。

    升序取 ``artifact-authority:<key>`` advisory xact 锁——备份、copy、闸内
    登记与失败恢复全程互斥，过期 promote 的恢复永远插不进新代次 promote
    的中途。锁序：artifact-authority:* → job-mutation:*（闸复查在同事务
    后段取后者；mutation 侧不取 artifact 锁，单项偏序不成环）。调用方须
    在写事务内、任何字节操作之前调用。"""
    for key in authority_keys:
        conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"artifact-authority:{key}",))


def register_rows_guarded(
    conn: DatabaseConnection,
    rows: list[dict[str, Any]],
    *,
    job_id: str,
    lease_id: str,
    staged_files: dict[str, Path] | None = None,
    job_dir: Path | None = None,
) -> list[dict[str, Any]] | None:
    """锁内复查 + 锁内落盘 + 清单行登记；None = 闸关（零写入）。

    调用方在写事务内调用（``promote_to_authority_guarded`` 的按 key 串行
    事务）：复查 lease 代次通过后先在锁内把 staged 文件 os.replace 进
    job_dir（已存在的目标先备份）、再 upsert 清单行——复查与登记之间没有
    reset 可介入的窗口。闸关时落盘与登记都不发生，调用方负责用备份恢复已
    完成的 authority copy 并按拒绝语义收尾。登记异常（upsert 失败）时已
    落盘的文件经 FilePromotionGuard 整体回滚、事务回滚清单行，异常原样上
    抛——调用方随后恢复 authority copy（#759 复审 P1-2）。
    """
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
    except BaseException:
        # #204 broad-except audit: compensate-then-bare-re-raise (#233
        # pattern), BaseException so even KeyboardInterrupt/SystemExit mid-
        # registration roll the landed files back before propagating. The
        # upsert loop's outcome space is the psycopg/DB surface (constraint
        # violation, dropped connection) plus programming errors; the file
        # promotion that already landed inside this transaction is not
        # transactional, so every flavor must roll it back via the guard
        # before the exception propagates (the caller then restores the
        # authority copies). The bare raise preserves the original type;
        # nothing is converted or masked.
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
    """备份 → copy → 锁内闸 + 登记 → 失败恢复 → 清理备份（两端共用）。

    全程一个事务：先按 key 升序取 artifact-authority advisory 锁，同一
    authority key 的备份/copy/闸内登记/失败恢复（commit 时刻失败除外，见
    残余面）与任何并发 promote 互斥（#759 复审 P1-C）——过期 promote 的
    恢复在构造上不可能踩掉新代次 promote 的字节。``rows[i]`` 登记
    ``copies[i]`` 的 authority key（调用方按同一批产物构造）。返回登记的
    清单行；None = 锁内闸拒——已 copy 的 authority key 已按回滚备份恢
    复、staged 文件未落盘、清单行未登记，残留 staging/无备份的
    authority 新对象是孤儿，lifecycle 兜底。备份/copy 中途失败与登记阶段
    抛异常（upsert/落盘失败）在事务死亡前于锁内恢复；commit 时刻失败
    （连接死亡）在锁外 best-effort 恢复——事务回滚后幸存的旧清单行永不
    指向 hash/size 不匹配的新字节（#759 复审 P1-2 与对抗复审 P1）。回滚
    备份的清理以其防范状态已解除为前提（登记提交、恢复成功或该 key 的
    copy 从未被尝试——ack 歧义下尝试过即视为可能已覆盖；codex #774 P1
    族）——锁外执行、只触本次调用 attempt 命名空间内的 key（调用方按调
    用唯一化 rollback key），并发重试的备份互不误删；恢复最终失败的备
    份保留（旧字节的最后恢复源，ERROR 日志带 key，GC 对 ``/.rollback/``
    段豁免——见 s3_jobs_gc）。

    残余面：DB 连接中途死亡（含 commit 时刻）时按 key 锁随会话释放，恢
    复降级为无串行的 best-effort；commit 歧义的另一半（ack 丢失、服务端
    实际已提交）下恢复会把旧字节盖回——选边偏向远更常见的 rollback
    half（连接死于 commit 到达前、序列化失败、死锁都是回滚），登记为不
    再收窄的残余面（docs/architecture/execution-generation.md §4）。
    """
    authority_keys = {spec.name: spec.authority_key for spec in copies}
    assert len(authority_keys) == len(copies), (
        f"duplicate artifact names in copies: {[spec.name for spec in copies]}"
    )
    backups: dict[str, str] = {}  # name -> rollback key of the pre-existing object
    promoted: list[str] = []
    restored = False
    unrecoverable: set[str] = set()  # 恢复最终失败的 name：其备份是最后恢复源，禁止清理
    try:
        with write_transaction(database_dsn) as conn:
            _lock_authority_keys_tx(conn, sorted(authority_keys.values()))
            try:
                for spec in copies:
                    if storage.head_object(spec.authority_key) is not None:
                        storage.copy_object(spec.authority_key, spec.rollback_key)
                        backups[spec.name] = spec.rollback_key
                for spec in copies:
                    # copy 尝试即入 promoted（ack 歧义：服务端可能已落字节
                    # 而响应丢失——尝试过就必须按「可能已覆盖」进恢复集，恢
                    # 复对未落地的 key 幂等无害；漏恢则其备份在 finally 被
                    # 当冗余删除，错位静默永久化，#774 对抗复审 P1）。
                    promoted.append(spec.name)
                    storage.copy_object(spec.staging_key, spec.authority_key)
                registered = register_rows_guarded(
                    conn,
                    rows,
                    job_id=job_id,
                    lease_id=lease_id,
                    staged_files=staged_files,
                    job_dir=job_dir,
                )
            except BaseException as exc:
                # #204 broad-except audit: in-transaction compensate-then-
                # bare-re-raise (#233 pattern), widened to BaseException so
                # KeyboardInterrupt/SystemExit mid-promote still run the
                # compensation before propagating — otherwise the finally
                # cleanup would delete backups whose guarded state was never
                # resolved (same family as codex #774 P1). Backup/copy/
                # registration failures (botocore family, Worker-untrusted
                # ValueErrors, psycopg/file surface) reach this arm before
                # __exit__: failures that leave the session alive (storage/
                # validation/file errors) still hold the per-key advisory
                # locks and the restore keeps the per-key serialization the
                # protocol promises (bounded retry absorbs transient flaps),
                # while a psycopg-surface or non-Exception failure means the
                # session (and its locks) is gone or unwinding — the same
                # restore then runs SINGLE-SHOT, because a backoff sleep
                # without serialization only widens the window where a late
                # restore stomps a newer committed promote (#774 对抗复审).
                # Either way it runs HERE, never after __exit__ (a restore
                # after tx death was the #759 对抗复审 P1 race). The bare
                # raise then rolls the transaction back; the outer arm skips
                # its own restore via ``restored`` (an unlocked second
                # restore could stomp a concurrent promote that took the
                # lock between the arms — suppressing it is correctness, not
                # mere idempotence). Original type propagates for caller
                # classification; nothing is converted or masked.
                attempts = (
                    _STORAGE_OP_ATTEMPTS
                    if isinstance(exc, Exception) and not isinstance(exc, psycopg.Error)
                    else 1
                )
                unrecoverable |= restore_authority_backups(
                    storage, promoted, backups, authority_keys, max_attempts=attempts
                )
                restored = True
                raise
            if registered is None:
                # 锁内闸拒：reset 落在入口预检与登记之间。按 key 锁内用备份
                # 恢复 authority copy——同 key 的新代次 promote 还在等锁，
                # 恢复绝不踩掉它的字节；幸存（或缺失）的旧清单行仍指向匹配
                # 的旧字节。会话健康、锁在握，恢复带界重试。
                unrecoverable |= restore_authority_backups(
                    storage, promoted, backups, authority_keys, max_attempts=_STORAGE_OP_ATTEMPTS
                )
                restored = True
    except BaseException:
        # #204 broad-except audit: outer arm, reached ONLY by (a) commit-time
        # failure — a connection death at ``__exit__`` (server rolls the
        # transaction back) is the one failure whose restore cannot ride the
        # per-key locks, the dead session already dropped them — and (b) a
        # BaseException interrupting a locked arm's restore BEFORE it set
        # ``restored`` (the unwind drops the session and its locks the same
        # way). Both share the same shape: locks gone, transaction state
        # unknown — so this restore is the documented unlocked best-effort
        # (docs §4), biased to the far-more-common rollback half of the
        # commit ambiguity, idempotent for keys the interrupted restore
        # already fixed, and SINGLE-SHOT: a backoff sleep here (locks gone)
        # only widens the stomp window (#774 对抗复审). BaseException so a
        # KeyboardInterrupt at commit still gets the biased restore rather
        # than deleting backups for an unresolved state. With-body failures
        # restored above while still locked and set ``restored``; an
        # unlocked second restore here could stomp a concurrent promote
        # that took the lock between the arms, so it is skipped rather than
        # merely idempotent. Bare raise preserves the original type for the
        # caller's classification.
        if not restored:
            unrecoverable |= restore_authority_backups(
                storage, promoted, backups, authority_keys, max_attempts=1
            )
        raise
    finally:
        for name, rollback_key in backups.items():
            # 删除前提（codex #774 P1）：备份只在它防范的状态已确认解除时
            # 才可删——登记提交（新字节权威化）、恢复成功（旧字节回位）或
            # 该 key 从未被覆盖（备份本就冗余）。恢复最终失败的备份是幸存
            # 清单行仍指向的旧字节的最后恢复源，保留并由上面的 ERROR 日志
            # 提供恢复指针。
            if name not in unrecoverable:
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
    """本地 lease 臂上传：字节先落 per-invocation staging key，再走共享 primitive。

    单产物版的 promote：调用方（``JobArtifactObjectStore.upload``）为每次
    调用派生独立 attempt 命名空间（staging/rollback key 都含 uuid）与清单
    行。闸拒返回 None——authority 对象已按回滚备份恢复，保留的旧清单行
    仍指向匹配的旧字节；staging key 在任何结局都清理（本地臂自建自删、
    key 私有；远端臂的 Worker staging 是共享协议落点，只在 finish 提交后
    由完成方删除，语义不同）。
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
