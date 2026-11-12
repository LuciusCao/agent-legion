"""已删 job 残留的 trash 暂存原语（#958 / #1113，文件预算自 ``job_deletion_trash``
拆出）。

只含纯文件系统操作：原子 rename 进 trash、锁外删除、空目录回收。事务语义与
调用次序（先提交后移动、锁下复核）见 ``job_deletion_trash`` 模块 docstring。
所有原语尽力而为、从不抛出：行已提交删除，清理失败只留可清扫的残留。
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# 已提交删除的证明：本模块在提交后、移入任何文件前先写入 operation 目录。
# TTL 回收只处理带此标记的条目——0.7.17 前 _restore_paths 在删除事务回滚、
# 原目录被重建时留下的恢复副本没有标记，其 jobs 行仍在、对无 job_artifacts
# 行的 legacy job 可能是唯一产物副本，一律不自动删除（只记日志提示人工处理）。
COMMITTED_MARKER = ".committed-deletion"


def stage_path(
    path: Path, trash_dir: Path, job_id: str, *, is_dir: bool = False, trash_name: str | None = None
) -> list[Path]:
    """把 ``path`` 原子 rename 进 ``trash_dir``（``trash_name`` 可携带相对层级，
    供含斜杠 node_key 的嵌套旧日志保持层级、避免 basename 互撞静默覆盖），
    返回暂存落点；不存在或任何 OSError 只记日志并返回空。"""
    staged = trash_dir / (trash_name if trash_name is not None else path.name)
    try:
        # 存在性探测也在 try 内：超长名（ENAMETOOLONG）等 OSError 只跳过本路径。
        if not (path.is_dir() if is_dir else path.exists()):
            return []
        staged.parent.mkdir(parents=True, exist_ok=True)
        # 先落已提交标记再移入：凡含本模块残留的 operation 目录必带标记。
        (trash_dir / COMMITTED_MARKER).write_text(f"{job_id}\n", encoding="utf-8")
        # 锁下只做原子 rename；跨文件系统（EXDEV）不退化为拷贝，留原位残留。
        os.rename(path, staged)
    except OSError:
        # 行已提交删除：移不走只留原位残留，不让成功的删除变成 API 错误。
        # 探测 / os.rename / mkdir 只抛 OSError。
        logger.exception("Failed to stage %s of deleted job %s into trash", path, job_id)
        return []
    return [staged]


def remove_path(path: Path, job_id: str) -> None:
    """删除已暂存的路径；失败只记日志，残留由 trash TTL 回收。"""
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        logger.exception("Failed to purge staged %s of deleted job %s", path, job_id)


def prune_empty_parents(path: Path, stop: Path) -> None:
    """嵌套旧日志移走后，逐级回收腾空的父目录（止于 ``stop``，尽力而为）。

    ``rmdir`` 只对空目录生效：非空（还有别的日志）或任何 OS 错误即停，不抛出。
    """
    parent = path.parent
    while parent != stop and parent.is_relative_to(stop):
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent


def prune_empty_op_dir(path: Path) -> None:
    """operation 目录只剩已提交标记（或已空）时连同标记一起删掉。

    嵌套暂存（含斜杠 node_key 的旧日志按相对日志根的层级落位）留下的空目录
    先自深向浅清掉；任何非空目录 / OS 错误都保留现场，交给 TTL 回收。
    """
    try:
        if not path.exists():
            return
        for child in sorted(path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if child.is_dir() and not child.is_symlink():
                with contextlib.suppress(OSError):
                    child.rmdir()
        if all(child.name == COMMITTED_MARKER for child in path.iterdir()):
            (path / COMMITTED_MARKER).unlink(missing_ok=True)
            path.rmdir()
            parent = path.parent
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
    except OSError:
        pass
