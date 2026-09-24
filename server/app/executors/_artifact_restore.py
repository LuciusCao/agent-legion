"""产物 promote 的补偿原语：authority 恢复与暂存/备份对象清理。

自 ``_artifact_promotion`` 拆出的体积预算姊妹模块（叶子模块：只依赖
storage 抽象，不反向引用编排层）。存储面重试策略常量由本模块持有，编排
层的上传重试（``put_stream_with_retries``）与这里的恢复重试共享同一套
策略。

**删除前提纪律**（codex #774 P1 族，docs/architecture/execution-
generation.md §2.8 补偿资源表）：``restore_authority_backups`` 返回最终
失败的 name 集，其备份对象是幸存清单行所指向旧字节的最后恢复源——调
用方的清理必须保留它们；``discard_object`` 只用于前提已确认解除的对
象（登记提交、恢复成功或 copy 从未被尝试）。
"""

from __future__ import annotations

import logging
import time

from server.app.storage import ObjectStorage

logger = logging.getLogger(__name__)

_STORAGE_OP_ATTEMPTS = 3
_STORAGE_OP_BACKOFF_SECONDS = 0.5


def restore_authority_backups(
    storage: ObjectStorage,
    promoted: list[str],
    backups: dict[str, str],
    authority_keys: dict[str, str],
    *,
    max_attempts: int,
) -> set[str]:
    """Re-overwrite already-promoted authority keys from their rollback backups.

    Shared by the mid-batch copy failure path and the stale write-gate path.
    Per-key bounded retry absorbs transient storage flaps in place, but only
    where the caller still holds the per-key locks (``max_attempts``): a
    backoff sleep WITHOUT that serialization just widens the window where a
    late restore stomps a newer committed promote (#774 对抗复审). The return
    value is the set of names whose restore ultimately failed — their backup
    objects are the LAST recovery source for the old bytes the surviving
    manifest rows still point at, so the caller's cleanup MUST keep them
    (codex #774 P1: unconditional deletion made the row/bytes mismatch
    permanent). Keys without a backup had no prior object (the orphan is
    lifecycle's backstop)."""
    failed: set[str] = set()
    for name in promoted:
        rollback_key = backups.get(name)
        if rollback_key is None:
            continue
        for attempt in range(max_attempts):
            try:
                storage.copy_object(rollback_key, authority_keys[name])
                break
            except Exception:
                # #204 broad-except audit: best-effort per-key restore inside the
                # compensation path — the outcome space is the storage layer
                # (botocore surface), and per-key containment is the point: the
                # bounded retry absorbs transient flaps, an ultimately-failing
                # key is logged at ERROR with its backup key (the last recovery
                # source, deliberately retained by the caller), the remaining
                # keys are still attempted, and the traceback rides the final
                # log (still inside the except handler).
                if attempt + 1 < max_attempts:
                    time.sleep(_STORAGE_OP_BACKOFF_SECONDS * (2**attempt))
                    continue
                failed.add(name)
                logger.error(
                    "artifact restore failed permanently for %s; retaining rollback"
                    " backup %s as the last recovery source",
                    authority_keys[name],
                    rollback_key,
                    exc_info=True,
                )
    return failed


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
