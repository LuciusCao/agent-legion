"""upgrade-workflow 重置闭包的产物清理编排（issue #645 review P1-3）。

与 ``job_rerun`` / ``job_execution.run_to`` 的 ``#508`` 清理三件套同款：
事务前可逆暂存重置闭包的本地产物 → ``job_artifacts`` 清单行在同一
事务内删除（mutation 层）→ 提交后 ``commit`` 暂存件（彻底删除）+
对象存储 best-effort 删除。本模块只编排放置顺序与回滚安全网，清单行
删除本身在 ``workflow_upgrade_mutation_inherit`` 的事务里。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from server.app.services.job_artifact_mutation import JobArtifactMutationService, StagedOutputs
from server.app.services.job_staged_cleanup import (
    commit_staged_outputs,
    delete_rerun_artifact_objects,
)

if TYPE_CHECKING:
    from server.app.workflows.definition import WorkflowDefinition

logger = logging.getLogger(__name__)


def stage_upgrade_reset_outputs(
    artifact_mutation: JobArtifactMutationService | None,
    job: dict[str, Any],
    definition: WorkflowDefinition,
    inherit_nodes: frozenset[str],
) -> StagedOutputs | None:
    """可逆暂存重置闭包的本地输出（继承集为空即 clean 模式，全量暂存）。

    服务未装配 ``artifact_mutation``（裸构造）时跳过本地暂存——清单行
    清理仍生效，本地旧文件由节点重跑自然覆盖（旧行为的安全子集）。
    RMW 产物（同时是输入与输出）不在 ``stage_outputs`` 的暂存面内，
    与 rerun 语义一致（#114）。
    """
    if artifact_mutation is None:
        return None
    reset_keys = [key for key in definition.executable_nodes if key not in inherit_nodes]
    if not reset_keys:
        return None
    return artifact_mutation.stage_outputs(job, reset_keys, definition)


def rollback_upgrade_staged_outputs(staged: StagedOutputs | None) -> None:
    """任何升级失败臂的暂存回滚（幂等；None 直通）。"""
    if staged is not None:
        staged.rollback()


def finalize_upgrade_staged_outputs(
    staged: StagedOutputs | None,
    object_store: Any,
    deleted_rows: list[dict[str, Any]],
    job_id: str,
) -> None:
    """提交后的收尾：暂存件彻底删除 + 对象存储 best-effort 清理。"""
    commit_staged_outputs(staged, job_id, "upgrade-workflow")
    delete_rerun_artifact_objects(object_store, deleted_rows, job_id, "upgrade-workflow")
