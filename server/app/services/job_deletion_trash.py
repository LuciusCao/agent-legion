"""Job 删除的事务外本地清理与 ``.trash`` TTL 回收（#958）。

DB 是删除的唯一权威：``JobDeletionService`` 先在
``lease_guarded_mutation`` 内删除 jobs 行并提交，提交成功后才由本模块在事务
外处理本地 job_dir 与节点日志——它们只是执行暂存与可淘汰缓存，产物权威副本在
对象存储（EXEC-ARTIFACT-STORE-001），所以本地清理失败不影响删除结果。

选「先提交后移动」而非「先移动后提交、失败反向补偿」：后者移动时 job 尚未
被 job-mutation 锁住，派发可能往刚被移走的目录写入、使补偿撞上重建目录；进程
崩溃于移动与提交之间更会留下「行仍在、缓存却躺在 trash」的活 job，TTL 回收
随后吞掉它的本地缓存。前者任何失败只会多留一份无行引用的缓存，DB 与对象存储
始终一致。各失败点的终态：

- 事务失败（冲突 / 外键 / DB 错误）：行仍在，文件系统零改动；
- 提交成功、移入 trash 失败，或进程崩溃于提交与移入之间：删除仍判成功，
  残留留在原路径（无自动回收；job id 由 workspace/workflow/source 确定性派生，
  同源重建的 job 会复用该路径，见 docs/data-layout.md）；
- 移入 trash 成功、删除失败或进程崩溃：残留在 ``.trash/<operation_id>/``，
  由 ``sweep_deletion_trash`` 按 TTL 回收。

先 rename 进 trash 再删（而非原地 rmtree）：原路径瞬间腾空，同 id 重建的 job
不会看到删了一半的旧目录；半途失败的残留集中在可清扫的位置。``.trash`` 不
提供恢复：提交后 jobs 行已不存在，残留没有可恢复到的归属。
"""

from __future__ import annotations

import glob
import logging
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from server.app.settings import Settings
from server.app.storage_paths import ManagedPathError, resolve_job_dir

logger = logging.getLogger(__name__)

TRASH_DIRNAME = ".trash"
# 提交后 trash 里只有已删 job 的残留，TTL 仅是给仍在 rmtree 的删除操作留的
# 宽限窗口（避免清扫与之抢删）；按 operation 目录 mtime（移入时刷新）计龄。
DELETION_TRASH_TTL = timedelta(hours=24)


def jobs_trash_root(settings: Settings) -> Path:
    return settings.jobs_dir / TRASH_DIRNAME


def logs_trash_root(settings: Settings) -> Path:
    return settings.logs_dir / "jobs" / TRASH_DIRNAME


def purge_deleted_job_files(job: Mapping[str, Any], settings: Settings, operation_id: str) -> None:
    """提交后把已删 job 的本地目录与日志移入 trash 再删除；失败只记日志。

    跨事务动作前重新校验目标：job_dir 按快照行重新解析（重新走 managed-root
    包含校验，防止事务期间路径被换成逃逸的链接）；日志在此刻才 glob，
    覆盖到提交前最后写入的文件（提交时 lease guard 已保证无运行中节点）。
    """
    job_id = str(job["id"])
    staged: list[Path] = []
    try:
        storage_dir: Path | None = resolve_job_dir(job, settings.jobs_dir)
    except ManagedPathError:
        logger.warning("Skip local cleanup of deleted job %s: storage_dir escapes", job_id)
        storage_dir = None
    if storage_dir is not None and storage_dir.is_dir():
        staged += _stage(storage_dir, jobs_trash_root(settings) / operation_id, job_id)
    for log_path in sorted(glob.glob(str(settings.logs_dir / "jobs" / f"{job_id}-*.log"))):
        staged += _stage(Path(log_path), logs_trash_root(settings) / operation_id, job_id)
    for path in staged:
        _remove(path, job_id)
    _prune_empty(jobs_trash_root(settings) / operation_id)
    _prune_empty(logs_trash_root(settings) / operation_id)


def sweep_deletion_trash(
    settings: Settings,
    now: datetime | None = None,
    ttl: timedelta = DELETION_TRASH_TTL,
) -> int:
    """删除 mtime 早于 ``now - ttl`` 的 ``.trash/<operation_id>`` 条目，返回删除数。

    jobs 与 logs 两个 trash 根都扫；条目无论内容一律按龄回收（含 0.7.17 前
    回滚冲突保留的副本——本地只是缓存，权威在对象存储）。单条失败只记日志，
    下一轮维护重试。
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
                if entry.is_dir() and not entry.is_symlink():
                    shutil.rmtree(entry)
                else:
                    entry.unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to purge deletion trash %s", entry, exc_info=True)
                continue
            removed += 1
    return removed


def _stage(path: Path, trash_dir: Path, job_id: str) -> list[Path]:
    staged = trash_dir / path.name
    try:
        trash_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(staged))
    except OSError:
        # 行已提交删除：移不走只留原位残留（见模块 docstring），不让成功的
        # 删除变成 API 错误。shutil.move / mkdir 只抛 OSError。
        logger.exception("Failed to stage %s of deleted job %s into trash", path, job_id)
        return []
    return [staged]


def _remove(path: Path, job_id: str) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        # 残留留在 .trash/<operation_id>，由 sweep_deletion_trash 按 TTL 回收。
        logger.exception("Failed to purge staged %s of deleted job %s", path, job_id)


def _prune_empty(path: Path) -> None:
    try:
        if path.exists() and not any(path.iterdir()):
            path.rmdir()
            parent = path.parent
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
    except OSError:
        pass
