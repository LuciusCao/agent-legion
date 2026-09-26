"""finish 闸内的结果文件提升臂 + 失败兜底转换（#759 对抗复审 P2 族）。

自 ``_lease_lifecycle`` 拆出的文件预算姊妹模块：``finish_lease`` 的
``staged_file_moves`` 臂只留一行调用；这里持有「提升失败不炸穿 finish
事务」的收尾策略——提升经 ``_file_promotion`` 的 guard 整体回滚（零半
应用现场），completed 转 failed 让 finish 照常提交。
"""

from __future__ import annotations

import logging
from dataclasses import replace

from server.app.executors._file_promotion import promote_result_staged_moves
from server.app.executors.models import ExecutionResult

logger = logging.getLogger(__name__)


def promote_result_staged_moves_contained(
    result: ExecutionResult, *, lease_id: str
) -> ExecutionResult:
    """提升 ``result.staged_file_moves`` 并返回应继续 finish 的 result。

    成功返回原 result。提升失败时 guard 已整体回滚：completed 转 failed
    照常提交——上抛会回滚整个 finish 事务，lease 不得释放、节点停在
    running，重试撞同一现场毒化成循环。两臂都置空 ``run_dir``（codex
    #774 P2）：回滚已撤销全部落盘（含 events.jsonl），从暂存视图探出的
    run_dir 随之失效——置空让 ``canonicalize_finish_paths`` 回退到文件
    系统派生（只记录真实存在的路径），而不是持久化一个随 staging 目录
    删除/指向旧执行日志的路径。非 completed 结果保留原状态与错误信息
    （原错误更有诊断价值，提升失败只进日志）。
    """
    try:
        promote_result_staged_moves(result.staged_file_moves)
    except Exception as exc:
        # #204 broad-except audit: 闸内字节面的最后兜底。提升的失败空间是
        # 文件系统面（os.replace/mkdir 的 OSError 族——预检无锁盖不住的跨
        # 节点 finish 竞态在现场留下的形状冲突、磁盘/权限故障）加规划校验
        # 的 ValueError，每一种都意味「这次结果的文件落不了盘」；guard 已
        # 整体回滚，无半应用现场，转换是安全的。traceback 随 warning 保全。
        logger.warning(
            "result file promotion failed inside finish gate (lease %s): %s",
            lease_id,
            exc,
            exc_info=True,
        )
        if result.status == "completed":
            return replace(
                result,
                status="failed",
                exit_code=1,
                error_message=f"failed to promote result files: {exc}",
                run_dir="",
            )
        return replace(result, run_dir="")
    return result
