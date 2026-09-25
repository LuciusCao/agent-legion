"""upgrade-workflow 的产物清理收尾编排（issue #645 review P1-3）。

与 ``job_rerun`` / ``job_execution.run_to`` 的 ``#508`` 清理三件套同款：
事务内可逆暂存重置闭包的本地产物 → ``job_artifacts`` 清单行在同一
事务内删除（mutation 层）→ 提交后 ``commit`` 暂存件（彻底删除）+
对象存储 best-effort 删除。事务内的暂存编排（含输入保护计划的
fail-closed 闸，#759 复审 P1-A）在 ``job_workflow_upgrade_staging``
（文件预算拆分）；本模块只剩失败臂回滚与提交后收尾（含「缺席即闸」名
的本地复活 sweep，``job_workflow_upgrade_sweep``）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.services.job_artifact_mutation import StagedOutputs
from server.app.services.job_staged_cleanup import (
    commit_staged_outputs,
    delete_rerun_artifact_objects,
)
from server.app.services.job_workflow_upgrade_sweep import sweep_absent_input_files

if TYPE_CHECKING:
    from pathlib import Path


def rollback_upgrade_staged_outputs(staged: StagedOutputs | None) -> None:
    """任何升级失败臂的暂存回滚（幂等；None 直通）。"""
    if staged is not None:
        staged.rollback()


def finalize_upgrade_staged_outputs(
    staged: StagedOutputs | None,
    object_store: Any,
    deleted_rows: list[dict[str, Any]],
    job_id: str,
    *,
    job: dict[str, Any] | None = None,
    jobs_dir: Path | None = None,
    sweep_names: frozenset[str] | set[str] = frozenset(),
    sweep_producers: dict[str, list[str]] | None = None,
    job_db: Any = None,
) -> None:
    """提交后的收尾：缺席名复活 sweep + 暂存件彻底删除 + 对象存储 best-effort 清理。"""
    if job is not None and jobs_dir is not None and job_db is not None:
        sweep_absent_input_files(job_db, job, jobs_dir, sweep_names, sweep_producers or {}, job_id)
    commit_staged_outputs(staged, job_id, "upgrade-workflow")
    delete_rerun_artifact_objects(object_store, deleted_rows, job_id, "upgrade-workflow")
