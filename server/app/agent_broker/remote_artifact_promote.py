"""Promote-phase orchestration for the Worker-direct S3 artifact channel (#160 D12).

Split out of ``remote_artifacts.py`` for the file-size budget: the result-commit
module stays the verify-then-apply orchestrator; this module owns the apply
phase's per-execution wiring — authority/staging/rollback key derivation and
the entry pre-check — and delegates the byte-plane sequence itself (rollback
backup → staging→authority copy → in-transaction gated registration → restore
on rejection, the whole per-key sequence serialized by a per-authority-key
advisory lock) to the shared primitive
``executors._artifact_promotion.promote_to_authority_guarded`` (#759 review
P1-B), which the local lease-arm upload uses identically.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from server.app.agent_broker.remote_artifact_support import build_manifest_rows
from server.app.executors._artifact_promotion import (
    AuthorityCopy,
    promote_to_authority_guarded,
)
from server.app.executors._artifact_restore import discard_object
from server.app.services.job_artifact_gzip import GZIP_SUFFIX, is_gzip_key
from server.app.services.job_artifact_objects import (
    JobArtifactObjectStore,
    artifact_staging_key,
    artifact_storage_key,
)

logger = logging.getLogger(__name__)


def promote_all(
    object_store: JobArtifactObjectStore,
    workspace_id: str,
    job_id: str,
    node_key: str,
    job_dir: Path,
    remote: dict[str, Any],
    staged: dict[str, Path],
    content_hashes: dict[str, str],
    execution_id: str,
    lease_id: str,
) -> bool:
    """Copy to authority keys, promote staged files, register rows.

    Undeclared names are promoted/registered but never land in the job dir
    (the same whitelist as the tar unpack path). Copies precede row writes:
    a failure between them leaves orphaned authority objects (lifecycle
    backstop), never dangling manifest rows. All manifest rows upsert in ONE
    transaction (the guarded registration): a mid-batch failure rolls back
    instead of leaving a half-registered manifest. Worker staging objects
    are NEVER deleted here — concurrent /result retries still need them
    between the first committer's promote and its finish (lifecycle and
    s3_jobs_gc reap them on every outcome, #774 对抗复审 P1).

    Re-runs overwrite existing authority keys, so every pre-existing
    authority object is first backed up (server-side copy to a per-invocation
    rollback key under this execution's staging prefix, no byte downloads). A
    mid-batch copy failure or a rejected registration restores the
    already-overwritten keys from their backups (bounded retry while the
    per-key locks are held; single-shot once they are gone) — otherwise the
    old manifest rows would keep pointing at objects whose bytes no longer
    match the recorded hash/size. Backup keys are unique per invocation
    (concurrent /result retries never share them). Cleanup follows the
    deletion precondition (codex #774 P1): a backup is discarded only once
    the state it guards is resolved (registration committed, restore
    succeeded, or its copy was never attempted); a backup whose restore
    ultimately failed is RETAINED as the last recovery source (ERROR log
    carries the key; s3_jobs_gc exempts the ``/.rollback/`` segment).

    #338: the authority key keeps the staging ref's form marker (``.gz`` or
    bare). A form-changing re-run targets a key that does not exist yet, so
    nothing is overwritten or backed up; the previous-form object stays for
    the still-pointing manifest row until the row upsert retargets it.

    #645 P2-a (EXEC-GENERATION-001 artifact byte plane): the promote runs
    BEFORE the finish generation CAS, so the write gate
    (``lease_artifact_write_current``) guards it twice — a lock-free pre-check
    here before any byte copy, and the authoritative re-check inside the
    shared primitive's registration transaction (job-dir file promotion and
    manifest row writes ride the same job-mutation lock, so no reset can
    intervene between the check and the row writes). Returns False when the
    gate rejects: nothing was copied/registered, or the copies were already
    restored from their backups; the caller turns this into the commit
    path's rejection semantics. Staging objects survive every promote
    outcome — success included — for the lifecycle/GC backstop and for
    concurrent retries (see above).
    """
    assert object_store.storage is not None
    storage = object_store.storage
    if not object_store.artifact_write_gate_open(job_id=job_id, lease_id=lease_id):
        logger.info(
            "discarding stale promote pre-copy (lease %s): job=%s node=%s",
            lease_id,
            job_id,
            node_key,
        )
        return False
    authority_keys = {
        name: artifact_storage_key(workspace_id, job_id, name)
        + (GZIP_SUFFIX if is_gzip_key(str(ref["storage_key"])) else "")
        for name, ref in remote.items()
    }
    # 回滚备份落 per-invocation key（codex #774 P1）：并发 /result 重试
    # （同 execution、同名）若共享 rollback key，先提交者的锁外清理会删掉
    # 后者的备份，后者闸拒/登记失败时恢复无备份可取——旧清单行指向新字
    # 节。staging key 是协议固定的 Worker 上传落点（同一 execution 的重试
    # 携带相同 refs、字节相同），不引入 attempt 维度；相应地成功路径也
    # 绝不删除 staging 源——重试在其 promote 与 finish 之间仍需读到它
    # （#774 对抗复审 P1），残留统一交 lifecycle/GC。
    attempt = uuid4().hex
    registered = promote_to_authority_guarded(
        storage,
        object_store.database_dsn,
        job_id=job_id,
        lease_id=lease_id,
        copies=[
            AuthorityCopy(
                name=name,
                staging_key=str(ref["storage_key"]),
                authority_key=authority_keys[name],
                rollback_key=artifact_staging_key(
                    workspace_id, job_id, execution_id, f".rollback/{attempt}/{name}"
                ),
            )
            for name, ref in remote.items()
        ],
        rows=build_manifest_rows(
            workspace_id, job_id, node_key, remote, authority_keys, content_hashes
        ),
        staged_files=staged,
        job_dir=job_dir,
    )
    if registered is None:
        # The stale write gate rejected the registration: a sweep/reset
        # committed between the pre-check and here. The shared primitive
        # already restored the byte copies from their backups, so the old (or
        # absent) authority objects keep matching the surviving manifest rows;
        # no job-dir writes and no row registrations happened.
        logger.info(
            "discarded stale promote post-copy (lease %s): job=%s node=%s",
            lease_id,
            job_id,
            node_key,
        )
        return False
    # promote 自身绝不删 staging 源（#774 对抗复审 P1）：promote 提交与
    # finish 之间隔着 mirror 上传与校验，并发 /result 重试此刻仍要
    # verify/promote 同一份 staging 字节——删源会让后到者的 HEAD 核验看
    # 到虚假存储故障，其失败 finish 抢在成功 finish 之前把已完成节点永
    # 久冤判 failed。staging 的唯一安全删除点是 finish 提交之后（由
    # completion_staged 的失败/成功收尾经 ``discard_staging_refs`` 执
    # 行）；其余结局（预检判死、verify 失败、闸拒、进程崩溃）的残留由
    # bucket lifecycle / GC 兜底。
    return True


def discard_staging_refs(
    object_store: JobArtifactObjectStore,
    output_artifacts: dict[str, Any],
    remote_names: set[str] | list[str],
) -> None:
    """finish 提交后的 Worker staging 源清理（#774 对抗复审：唯一安全删
    除点——迟到重试的 verify 即使撞见缺源，其 finish 因 lease 已释放拿
    到 verdict False，伤不到已提交节点）。"""
    storage = object_store.storage
    if storage is None:
        return
    for name in remote_names:
        ref = output_artifacts.get(name)
        if isinstance(ref, dict):
            discard_object(storage, str(ref["storage_key"]))
