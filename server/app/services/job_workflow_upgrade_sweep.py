"""upgrade 提交后的「缺席即闸」名本地文件复活 sweep（#759 复审 P1-A）。

hydration（``workflow_worker/input_hydration.py``）刻意不取 job-mutation
锁：它可能在升级事务提交前从旧清单行复活本地产物且代次复查恰好通过
（残余窗口，见 docs/architecture/execution-generation.md §5）。对三面
已删的缺席判定名（保护计划的 ``sweep`` 集），提交后立即按名再删一次
本地文件——恢复写先于代次复查（``hydrate_job_artifacts`` 的顺序），凡
复查通过的复活必落在提交前，本 sweep 在提交后执行必然覆盖；复查落在
提交后的恢复会自我丢弃。best-effort：单文件失败不中断其余（残留由
§5 残余面论证兜底），绝不让已提交的成功升级抛错。

codex #776 复审 P2-A：提交后作业立即可被调度，重置节点的新 attempt
可能在 sweep 前已写出同名新字节——删除前必须锁内复核
（``JobQueries.sweep_delete_guard``：job-mutation 锁内查清单行与生产者
节点状态，claim 侧同锁，判定到删除之间 claim 不可插入），有新代次
证据（清单行已重登记 / 生产者 running/completed）的名跳过删除。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from server.app.storage_paths import resolve_job_dir

logger = logging.getLogger(__name__)


def sweep_absent_input_files(
    job_db: Any,
    job: dict[str, Any],
    jobs_dir: Path,
    sweep_names: frozenset[str] | set[str],
    producers: dict[str, list[str]],
    job_id: str,
) -> None:
    """提交后删除 sweep 集中名字的本地文件（锁内复核保护集后，存在即删）。"""
    if not sweep_names:
        return
    try:
        job_dir = resolve_job_dir(job, jobs_dir)
    except ValueError:
        # job_dir 解析失败（ManagedPathError 是 ValueError 子类）：sweep
        # 是 best-effort 的提交后收尾，失败只降级为残余面内的旧文件。
        logger.warning("sweep skipped for job %s: job dir unresolvable", job_id, exc_info=True)
        return
    try:
        with job_db.sweep_delete_guard(job_id, sweep_names, producers) as protected:
            for name in sorted(set(sweep_names) - set(protected)):
                path = (job_dir / name).resolve()
                try:
                    path.relative_to(job_dir)
                except ValueError:
                    # 名字逃逸 job_dir（与暂存侧同款路径纪律）：跳过该名。
                    logger.warning("sweep skips escaping name %r for job %s", name, job_id)
                    continue
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    # per-file 收容：删除失败不中断其余名字；残留文件由残余面
                    # 论证兜底（下一轮评估该名清单行已消失，hydration 不再恢复）。
                    logger.warning("sweep failed for %r of job %s", name, job_id, exc_info=True)
    except Exception:
        # #204 broad-except audit: sweep 是已提交升级的 best-effort 收尾——
        # 保护集复核的 DB 读失败（连接/池故障，非业务异常族）时跳过整个
        # sweep 也不能让已提交的成功升级抛错（500 会误报成功、批量调用方
        # 中断后续 job）；残留旧文件由 §5 残余面论证兜底（清单行已删，
        # hydration 不再恢复）。logger.exception 保留 traceback。
        logger.exception("sweep guard failed for job %s; sweep skipped", job_id)
