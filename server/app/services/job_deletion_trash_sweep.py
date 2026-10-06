"""``.trash`` 的 TTL 回收（#958）：只删带已提交标记的 job 删除残留。

标记语义与失败点终态见 ``job_deletion_trash`` 模块 docstring；由 workflow
worker 周期维护（``WorkflowMaintenance._run_cleanup``）调用。
"""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime, timedelta

from server.app.services.job_deletion_trash import (
    COMMITTED_MARKER,
    DELETION_TRASH_TTL,
    jobs_trash_root,
    logs_trash_root,
)
from server.app.settings import Settings

logger = logging.getLogger(__name__)


def sweep_deletion_trash(
    settings: Settings,
    now: datetime | None = None,
    ttl: timedelta = DELETION_TRASH_TTL,
) -> int:
    """删除带已提交标记、mtime 早于 ``now - ttl`` 的 ``.trash/<operation_id>``
    条目，返回删除数。

    jobs 与 logs 两个 trash 根都扫。只有本模块写入的、带 ``COMMITTED_MARKER``
    的 operation 目录可证明属于已提交删除；无标记的条目（0.7.17 前回滚冲突
    保留的恢复副本、symlink、手工放入的文件）永不自动删除，超龄时记 warning
    提示人工检查。单条失败只记日志，下一轮维护重试。
    """
    cutoff = ((now or datetime.now(UTC)) - ttl).timestamp()
    removed = 0
    for root in (jobs_trash_root(settings), logs_trash_root(settings)):
        try:
            entries = sorted(root.iterdir()) if root.is_dir() else []
        except OSError:
            logger.warning("Cannot list deletion trash %s", root, exc_info=True)
            continue
        for entry in entries:
            try:
                if entry.lstat().st_mtime >= cutoff:
                    continue
                if entry.is_symlink() or not (entry / COMMITTED_MARKER).is_file():
                    logger.warning(
                        "Deletion trash entry %s has no committed-deletion marker "
                        "(pre-0.7.17 rollback copy?); not purging, inspect manually",
                        entry,
                    )
                    continue
                shutil.rmtree(entry)
            except OSError:
                logger.warning("Failed to purge deletion trash %s", entry, exc_info=True)
                continue
            removed += 1
    return removed
