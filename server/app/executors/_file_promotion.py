"""可回滚的锁内本地文件提升（#759 复审 P1-2/P1-1）。

自 ``_artifact_promotion`` 拆出的文件预算姊妹模块：产物清单登记
（``register_rows_guarded``）与 lease finish（``finish_lease`` 的
``staged_file_moves`` 臂）共用同一份「先备份旧目标、整体提升、失败整体
回滚、成功丢弃备份」纪律——两个调用面都不允许留下半应用的 job_dir。
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class FilePromotionGuard:
    """可回滚的锁内文件提升句柄。

    登记事务内把 staged 文件 os.replace 进目标位置前，先把每个已存在的
    目标改名为同目录临时备份；``rollback``（异常路径）反向恢复——新文件
    移除、旧文件归位，不留半应用现场；``discard``（成功）丢弃备份。两个
    收尾都幂等。
    """

    def __init__(self) -> None:
        self._moved: list[tuple[Path, Path | None]] = []  # (target, backup)
        self._backup_dir: Path | None = None
        self._settled = False

    def rollback(self) -> None:
        """反向恢复已提升的文件；自身失败的单项只告警、不中断其余恢复。"""
        if self._settled:
            return
        self._settled = True
        for target, backup in reversed(self._moved):
            try:
                if backup is not None:
                    _replace_file(backup, target)
                else:
                    target.unlink(missing_ok=True)
            except OSError:
                logger.warning("failed to roll back promoted file %s", target, exc_info=True)
        if self._backup_dir is not None:
            shutil.rmtree(self._backup_dir, ignore_errors=True)

    def discard(self) -> None:
        """登记成功：丢弃旧文件备份（新文件已在目标位置）。"""
        if self._settled:
            return
        self._settled = True
        if self._backup_dir is not None:
            shutil.rmtree(self._backup_dir, ignore_errors=True)


def _replace_file(source: Path, target: Path) -> None:
    """os.replace with a cross-device fallback (P3 deployment edge: logs or
    jobs mounted on separate filesystems). The fallback is a copy+unlink —
    non-atomic, acceptable outside the single-data-dir default layout."""
    try:
        os.replace(source, target)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.move(str(source), str(target))


def promote_file_moves_guarded(
    moves: list[tuple[Path, Path]],
    *,
    backup_parent: Path,
) -> FilePromotionGuard:
    """执行 (target, source) 文件提升并返回可回滚句柄。

    提升中途失败时先自动回滚已移动的部分再原样上抛，绝不留下半应用
    的 job_dir。备份目录建在 ``backup_parent`` 下（与目标同文件系统，
    os.replace 才成立）。source 缺席而 target 在场按「已提升」幂等跳过
    （#759 review P2：finish 批事务整批回滚重放时，第一次尝试已把
    source 移走——staging 目录在调用方阻塞等待期间一直存活，source
    不可能因其他原因消失；跳过的条目不进回滚簿，保持第一次尝试的
    落盘结果）。

    完全相同的 (target, source) 对先去重（#759 对抗复审 P1）：finish 批
    重放与工作流重复声明 outputs（``outputs: ["out.json", "out.json"]``
    的笔误）都会产生相同对，是良性形态——重复声明的工作流不得被预检
    误杀成永久卡死。去重后仍重复 target 的（同 target 异 source）才是
    别名冲突，应用前 ValueError（#759 复审 P2）：两个逻辑产物名归一到
    同一路径（如 ``reports/out.json`` 与 ``reports//out.json`` 携不同
    staging 文件）时，若只凭「source 缺席 + target 在场」判定，第二项
    会被误当重放静默跳过——两条清单行指向两份 S3 字节，本地却只有一
    份文件。每个条目在可能失败的 source→target 替换之前登记回滚簿
    （同 P2）：替换失败时旧目标的备份可被回滚臂恢复，而不是连备份一
    起清掉。
    """
    guard = FilePromotionGuard()
    deduped: list[tuple[Path, Path]] = []
    seen_pairs: set[tuple[Path, Path]] = set()
    for pair in moves:
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            deduped.append(pair)
    seen_targets: set[Path] = set()
    for target, _source in deduped:
        if target in seen_targets:
            raise ValueError(f"duplicate promote target: {target}")
        seen_targets.add(target)
    moves = deduped
    if not moves:
        return guard
    backup_dir = Path(tempfile.mkdtemp(prefix=".promote-rollback-", dir=backup_parent))
    guard._backup_dir = backup_dir
    try:
        for index, (target, source) in enumerate(moves):
            target.parent.mkdir(parents=True, exist_ok=True)
            if not source.exists():
                if target.exists():
                    continue
                raise FileNotFoundError(f"staged source is missing: {source}")
            backup: Path | None = None
            if target.exists() or target.is_symlink():
                backup = backup_dir / str(index)
                _replace_file(target, backup)
            guard._moved.append((target, backup))
            _replace_file(source, target)
    except Exception:
        # #204 broad-except audit: compensate-then-bare-re-raise (#233
        # pattern). The move loop's outcome space is the filesystem surface
        # (OSError family from os.replace/mkdir — missing source, permission,
        # cross-device) plus programming errors, and every flavor must roll
        # back the already-moved files before propagating so no half-applied
        # job_dir survives; the bare raise preserves the original type for
        # the caller's classification, nothing is converted or masked.
        guard.rollback()
        raise
    return guard


def promote_result_staged_moves(staged_file_moves: tuple[tuple[str, str], ...]) -> None:
    """``ExecutionResult.staged_file_moves`` 的提升入口（finish 代次闸内调用）。

    提升失败整体回滚再上抛；成功即丢弃备份——同事务后续 SQL 失败的崩溃
    窗口残余见 docs/architecture/execution-generation.md §5.3。
    """
    moves = [(Path(target), Path(source)) for target, source in staged_file_moves]
    backup_parent = moves[0][0].parent
    backup_parent.mkdir(parents=True, exist_ok=True)
    guard = promote_file_moves_guarded(moves, backup_parent=backup_parent)
    guard.discard()
