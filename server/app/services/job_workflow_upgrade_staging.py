"""clean 升级的 staged 突变编排（#759 预算拆分自 ``job_workflow_upgrade``）。

mutation 锁内暂存 + 切换 revision + 提交后清理；清单行按暂存名精确删除
（同 rerun）——「保留 ⇔ 未暂存」构造性成立，无独立 preserve 集。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.jobs.workflow_upgrade_mutation import upgrade_job_workflow
from server.app.services.job_artifact_mutation import StagedOutputs
from server.app.services.job_staged_cleanup import (
    commit_staged_outputs,
    delete_rerun_artifact_objects,
)
from server.app.services.job_upgrade_stage_outputs import (
    rollback_quietly,
    stage_upgrade_outputs,
)
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.workflows.definition import WorkflowDefinition

if TYPE_CHECKING:
    from datetime import datetime

    from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService


def execute_staged_upgrade(
    service: JobWorkflowUpgradeService,
    job: dict[str, Any],
    job_id: str,
    active: dict[str, Any],
    definition: WorkflowDefinition,
    frozen_config_json: str | None,
    now: datetime,
) -> None:
    """mutation 锁内暂存 + 切换 revision + 提交后清理；冲突/失败一律回滚暂存。"""
    staged: list[StagedOutputs] = []
    try:
        with service.job_db.lease_guarded_mutation(
            job_id,
            now,
            reject_running_nodes=True,
        ) as conn:
            old_definition = definition_from_job_snapshot(job)
            staged = stage_upgrade_outputs(
                service.artifact_service, job, definition, old_definition
            )
            # 清单行按暂存名精确删除（同 rerun 的 mark_nodes_for_rerun）：
            # 「保留 ⇔ 未暂存」构造性成立，不再独立计算 preserve 集——
            # 矩阵未来再加行也不会开姊妹洞（#759 自审治本）。
            staged_names = set().union(*(handle.artifact_names for handle in staged))
            deleted_rows = upgrade_job_workflow(
                conn,
                job_id,
                workflow_revision_id=str(active["id"]),
                workflow_version=int(active["version"]),
                workflow_definition_hash=str(active["definition_hash"]),
                workflow_definition_snapshot_json=str(active["definition_json"]),
                node_keys=list(definition.executable_nodes),
                frozen_config_json=frozen_config_json,
                staged_artifact_names=staged_names,
            )
    except Exception:
        # #204 broad-except audit: staged filesystem + DB mutation sequence,
        # mirroring commit_rerun's terminal arm — the staged artifacts
        # (already moved off their original paths) must be rolled back
        # whatever failed (JobMutationConflict included; the caller
        # classifies it to skipped), otherwise outputs vanish from the job
        # dir while the DB is unchanged. per-item 兜住不中断其余补偿。
        for handle in staged:
            rollback_quietly(handle)
        raise
    for handle in staged:
        commit_staged_outputs(handle, job_id, "upgrade_workflow")
    delete_rerun_artifact_objects(service.object_store, deleted_rows, job_id, "upgrade_workflow")
