"""提交后退役对象的删除走查（codex #776 R7 P2-A；预算拆分自
``rerun_artifact_cleanup``）。

与 promote 共享 ``artifact-authority:<key>`` 锁（``queries.sweep_guard``
的 ``authority_delete_guard``）：在途 promote 已把新字节 copy 到稳定
authority key、但尚未在同一事务后段登记清单行时，无锁的清单探针必然
miss——直接删除会把新对象删掉，随后 promote 登记留下悬挂清单行。
持锁复核后删除封死该窗口；锁不可得（在途 promote 持有）时跳过该 key
（保守方向：旧对象成孤儿由 bucket lifecycle 兜底，绝不误删新字节）。
无 ``delete_objects_guarded`` seam 的 store（测试桩）走既有无锁路径。
"""

from __future__ import annotations

import logging
from typing import Any

from server.app.jobs.queries.sweep_guard import authority_delete_guard

logger = logging.getLogger(__name__)


def delete_objects_guarded(store: Any, rows: list[dict[str, Any]], job_id: str) -> set[str]:
    """逐 key 锁内复核后删除，返回实际删除的 storage_key 集。

    ``JobArtifactObjectStore.delete_objects_guarded`` 的实现体（方法仅委托，
    让调用方按 duck seam 探测）；storage 删除在锁持有期间完成——锁释放后
    删除会让 promote 在「删除前」backup/copy/登记、随后删除误伤新字节。
    """
    deleted: set[str] = set()
    storage = store.storage
    if storage is None:
        return deleted
    for row in rows:
        key = str(row["storage_key"])
        with authority_delete_guard(store.database_dsn, key) as locked:
            if not locked:
                # 在途 promote 持锁（copy 已落、登记未提交）：跳过，绝不误删。
                continue
            # 锁内复核清单（复用 store 的探针 seam，#683 的逐对象重验语义
            # 不变——锁把「复核到删除」串行化，探针到删除不再有窗口）。
            if key in set(store.live_keys_for(job_id, [key])):
                continue
            try:
                storage.delete_object(key)
            except Exception:
                # #204 broad-except audit: 提交后逐对象 best-effort 清理——
                # 单对象失败（对象存储 SDK/网络面，无业务异常族可收窄）只留
                # 孤儿对象（lifecycle 兜底），不中断其余 key、不上抛给已
                # 提交成功的调用方。锁随事务正常提交释放。
                logger.warning(
                    "guarded object removal failed for %s of job %s", key, job_id, exc_info=True
                )
                continue
            deleted.add(key)
    return deleted


def _live_keys(object_store: Any, job_id: str, keys: list[str]) -> set[str]:
    """Targeted manifest existence probe for ``keys`` ({} when the store
    exposes no query seam — the legacy degrade-to-removal-without-guard)."""
    probe = getattr(object_store, "live_keys_for", None)
    if probe is None:
        return set()
    return set(probe(job_id, keys))


def delete_retired_objects(
    object_store: Any, deleted_rows: list[dict[str, Any]], job_id: str, operation: str
) -> None:
    """The removal walk of ``delete_rerun_artifact_objects`` (raising body).

    R7 P2-A：有 ``delete_objects_guarded`` seam 的 store 走「共享
    artifact-authority 锁 + 锁内复核」臂——在途 promote（copy 已落、登记
    未提交）持锁期间跳过该 key（``deferred``：旧对象成孤儿由 lifecycle
    兜底）；legacy 臂维持原有的逐对象清单重验。
    """
    live = _live_keys(object_store, job_id, [str(row["storage_key"]) for row in deleted_rows])
    stale_rows = [row for row in deleted_rows if str(row["storage_key"]) not in live]
    if len(stale_rows) < len(deleted_rows):
        logger.info(
            "rerun %s cleanup for job %s skipped %d re-registered object(s)",
            operation,
            job_id,
            len(deleted_rows) - len(stale_rows),
        )
    spared: set[str] = set()
    deferred: set[str] = set()
    deleted_keys: set[str] = set()
    guarded = getattr(object_store, "delete_objects_guarded", None)
    for row in stale_rows:
        key = str(row["storage_key"])
        if guarded is not None:
            removed = guarded([row], job_id)
            if key in removed:
                deleted_keys.add(key)
            else:
                # 在途 promote 持锁或锁内复核到存活行：跳过删除（保守）。
                deferred.add(key)
            continue
        # Legacy 臂：Re-validate against the CURRENT manifest immediately
        # before this object's removal: a re-attempt completing promote_all
        # after the batch probe above re-registers this same stable authority
        # key, and removing it would strand the fresh manifest row.
        if key in _live_keys(object_store, job_id, [key]):
            spared.add(key)
            continue
        object_store.delete_objects([row])
        deleted_keys.add(key)
    if spared or deferred:
        logger.info(
            "rerun %s cleanup for job %s spared %d re-registered + %d lock-held object(s)",
            operation,
            job_id,
            len(spared),
            len(deferred),
        )
    # Post-removal re-check: a row appearing under a removed key in the
    # residual per-object window means the race fired — surface it (bucket
    # lifecycle cannot repair a stranded manifest row). Nothing removed →
    # trivially nothing raced, and the probe is skipped with it.
    if not deleted_keys:
        return
    raced = _live_keys(object_store, job_id, sorted(deleted_keys)) & deleted_keys
    if raced:
        logger.warning(
            "rerun %s cleanup for job %s: %d manifest row(s) appeared under "
            "just-removed object key(s) %s — re-attempt raced the cleanup; "
            "re-run the node or re-upload to repair the authority copy",
            operation,
            job_id,
            len(raced),
            sorted(raced)[:5],
        )
