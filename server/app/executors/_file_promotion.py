"""可回滚的锁内本地文件提升（#759 复审 P1-2/P1-1）。

自 ``_artifact_promotion`` 拆出的文件预算姊妹模块：产物清单登记
（``register_rows_guarded``）与 lease finish（``finish_lease`` 的
``staged_file_moves`` 臂）共用同一份「先备份旧目标、整体提升、失败整体
回滚、成功丢弃备份」纪律——两个调用面都不允许留下半应用的 job_dir。
回滚簿句柄与替换原语在叶子模块 ``_file_promotion_guard``（体积预算再拆
分；回滚簿的删除前提纪律见该模块 docstring）。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from server.app.executors._file_promotion_guard import FilePromotionGuard, _replace_file

# 测试 patch 缝隙：move 循环经本模块命名空间解析 ``_replace_file``（patch
# 点在本模块）；``FilePromotionGuard.rollback`` 的恢复调用走
# ``_file_promotion_guard`` 自己的命名空间（patch 点在那侧）。


def _is_real_directory(path: Path) -> bool:
    """真实目录（不含指向目录的 symlink——os.replace 对 symlink 本体的
    备份/替换/回滚都按文件语义成立，只有真实目录让替换与回滚双向不可逆）。

    同一谓词在 ``completion_preflight.find_landing_conflict`` 内联了一份
    （预检层，保持 agent_control 不 import 本模块）——改动须两侧同步。"""
    return path.is_dir() and not path.is_symlink()


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

    真实目录形态的 target/source 在任何移动之前拒绝（codex #774 P2）：
    回滚簿的可逆性只对文件成立——target 是目录时整棵目录会被挪进备份、
    成功收尾被 rmtree 递归删除（同目录下其他产物的本地副本随清单行仍在
    而消失），且回滚臂无法把目录 os.replace 回已存在的文件上；source 是
    目录时提升一半的现场同样无法经 unlink 回滚。预检零副作用，留给上层
    （completion preflight 的祖先畅通检查之外的形状面）以干净失败收尾。
    预检与移动之间的竞态残余（现场被并发写入换成目录）由备份后的立即
    复查收口：命中即整体回滚再拒绝，窗口只剩单次 rename 本身；source 侧
    的对称竞态（微秒级、需 staging 目录内的病态并发写）登记为残余不闭
    合。回滚自身部分失败时备份目录整体保留（最后恢复源，ERROR 日志带路
    径）——与 S3 侧备份的删除前提同族。
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
    for target, source in deduped:
        if _is_real_directory(target):
            raise ValueError(f"promote target is a directory: {target}")
        if _is_real_directory(source):
            raise ValueError(f"promote source is a directory: {source}")
    moves = deduped
    if not moves:
        return guard
    backup_parent.mkdir(parents=True, exist_ok=True)  # 预检通过之后才落任何副作用
    backup_dir = Path(tempfile.mkdtemp(prefix=".promote-rollback-", dir=backup_parent))
    guard.attach_backup_dir(backup_dir)
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
                if _is_real_directory(backup):
                    # 预检与移动之间现场被并发写入换成目录（运行中的沙箱进程
                    # 直写 job_dir 不经代次闸，docs §4 残余面 #1）——登记回滚
                    # 簿后整体回滚再拒绝，把窗口收拢到单次 rename 本身；绝不
                    # 走到成功收尾的 rmtree 毁整棵目录（codex #774 P2 的竞态
                    # 残余）。回滚自身失败时备份目录按删除前提保留。
                    guard.record_move(target, backup)
                    guard.rollback()
                    raise ValueError(f"promote target became a directory: {target}")
            guard.record_move(target, backup)
            _replace_file(source, target)
    except BaseException:
        # #204 broad-except audit: compensate-then-bare-re-raise (#233
        # pattern), BaseException so even KeyboardInterrupt/SystemExit mid-
        # loop rolls the moved files back — the caller-side guard variable
        # never receives the internal handle when this raises, so the
        # rollback must happen HERE. The move loop's outcome space is the
        # filesystem surface (OSError family from os.replace/mkdir — missing
        # source, permission, cross-device) plus programming errors, and
        # every flavor must roll back the already-moved files before
        # propagating so no half-applied job_dir survives; the bare raise
        # preserves the original type for the caller's classification,
        # nothing is converted or masked.
        guard.rollback()
        raise
    return guard


def promote_result_staged_moves(staged_file_moves: tuple[tuple[str, str], ...]) -> None:
    """``ExecutionResult.staged_file_moves`` 的提升入口（finish 代次闸内调用）。

    提升失败整体回滚再上抛；成功即丢弃备份——同事务后续 SQL 失败的崩溃
    窗口残余见 docs/architecture/execution-generation.md §4 第 3 条。
    """
    moves = [(Path(target), Path(source)) for target, source in staged_file_moves]
    if not moves:
        return
    backup_parent = moves[0][0].parent
    guard = promote_file_moves_guarded(moves, backup_parent=backup_parent)
    guard.discard()
