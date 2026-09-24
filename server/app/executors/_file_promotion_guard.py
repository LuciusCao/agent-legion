"""文件提升的回滚簿句柄与替换原语（#759 复审 P1-2/P1-1）。

自 ``_file_promotion`` 拆出的体积预算姊妹模块（叶子：只依赖文件系统）。
``FilePromotionGuard`` 持有 (target, backup) 簿与 ``rollback``/``discard``
两个幂等收尾；``_replace_file`` 是带跨设备 fallback 的 os.replace 包装，
驱动层（``_file_promotion``）与回滚簿共用。

**删除前提纪律**（codex #774 P1 族，docs/architecture/execution-
generation.md §2.8 补偿资源表）：备份目录只在全部恢复成功（rollback）
或登记成功（discard）时才整体删除——``rollback`` 部分失败保留目录并
ERROR 日志携带失败 target 与目录路径，失败项的备份文件是旧目标的最后
本地恢复源。
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
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

    def attach_backup_dir(self, backup_dir: Path) -> None:
        """登记备份目录（驱动层在 mkdtemp 之后、任何替换之前调用）。"""
        self._backup_dir = backup_dir

    def record_move(self, target: Path, backup: Path | None) -> None:
        """登记一条 (target, backup) 回滚簿——必须在可能失败的替换之前调用
        （替换失败时旧目标的备份可被回滚臂恢复，而不是连备份一起清掉）。"""
        self._moved.append((target, backup))

    def rollback(self) -> None:
        """反向恢复已提升的文件；自身失败的单项只告警、不中断其余恢复。

        删除前提与 S3 侧同族（codex #774 P1 族）：备份目录只在全部恢复成
        功时才整体删除——任何一项恢复失败（如 TOCTOU 下 target 被并发方换
        成非空目录），保留整个备份目录并 ERROR 日志携带失败 target 与目录
        路径：失败项的备份文件是旧目标的最后本地恢复源。恢复循环自身被
        BaseException 中断（KeyboardInterrupt/SystemExit）时同样保留目录
        并补 ERROR 指针；簿已封存不重试（调用方随即上抛，进程多半在退
        出）。"""
        if self._settled:
            return
        self._settled = True
        failed: list[Path] = []
        try:
            for target, backup in reversed(self._moved):
                try:
                    if backup is not None:
                        _replace_file(backup, target)
                    else:
                        target.unlink(missing_ok=True)
                except OSError:
                    failed.append(target)
                    logger.warning("failed to roll back promoted file %s", target, exc_info=True)
        except BaseException:
            # #204 broad-except audit: interruption pointer only — 恢复循环被
            # KeyboardInterrupt/SystemExit 打断时，簿内未恢复项的备份是旧目
            # 标的最后本地恢复源：备份目录必须保留且留下 ERROR 指针（否则只
            # 能靠 .promote-rollback-* glob 偶然发现）。原异常原样上抛，不
            # 吞不转；rmtree 因 raise 跳过，删除前提自然成立。
            if self._backup_dir is not None:
                logger.error(
                    "file-promotion rollback interrupted; retaining backup dir %s"
                    " as the last recovery source",
                    self._backup_dir,
                    exc_info=True,
                )
            raise
        if self._backup_dir is not None:
            if failed:
                logger.error(
                    "file-promotion rollback incomplete for %s; retaining backup dir %s"
                    " as the last recovery source",
                    [str(target) for target in failed],
                    self._backup_dir,
                )
            else:
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
