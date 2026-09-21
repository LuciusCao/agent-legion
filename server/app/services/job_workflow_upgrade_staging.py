"""clean 升级的产物暂存组合（#759 预算拆分自 ``job_workflow_upgrade``）。

clean 升级全量重跑，旧 revision 的全部产物一律失效：暂存集 = 新旧定义
可执行节点之并——旧定义独有的节点已不在新 revision 里，但其旧产物同样
不能留，否则隐式消费者会被旧输入文件立即解锁、读到上一轮结果。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.jobs.workflow_upgrade_mutation import upgrade_job_workflow
from server.app.services.job_artifact_mutation import JobArtifactMutationService, StagedOutputs
from server.app.services.job_staged_cleanup import (
    commit_staged_outputs,
    delete_rerun_artifact_objects,
)
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.workflows.definition import WorkflowDefinition

if TYPE_CHECKING:
    from datetime import datetime

    from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService


def stage_upgrade_outputs(
    artifact_service: JobArtifactMutationService,
    job: dict[str, Any],
    new_definition: WorkflowDefinition,
    old_definition: WorkflowDefinition | None,
) -> list[StagedOutputs]:
    """暂存新旧定义可执行节点之并的产物；每个 handle 独立 commit/rollback。"""
    staged = [
        artifact_service.stage_outputs(job, sorted(new_definition.executable_nodes), new_definition)
    ]
    if old_definition is not None:
        removed = sorted(
            set(old_definition.executable_nodes) - set(new_definition.executable_nodes)
        )
        if removed:
            staged.append(artifact_service.stage_outputs(job, removed, old_definition))
    return staged


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
            staged = stage_upgrade_outputs(
                service.artifact_service, job, definition, definition_from_job_snapshot(job)
            )
            deleted_rows = upgrade_job_workflow(
                conn,
                job_id,
                workflow_revision_id=str(active["id"]),
                workflow_version=int(active["version"]),
                workflow_definition_hash=str(active["definition_hash"]),
                workflow_definition_snapshot_json=str(active["definition_json"]),
                node_keys=list(definition.executable_nodes),
                frozen_config_json=frozen_config_json,
            )
    except Exception:
        # #204 broad-except audit: staged filesystem + DB mutation sequence,
        # mirroring commit_rerun's terminal arm — the staged artifacts
        # (already moved off their original paths) must be rolled back
        # whatever failed (JobMutationConflict included; the caller
        # classifies it to skipped), otherwise outputs vanish from the job
        # dir while the DB is unchanged.
        for handle in staged:
            handle.rollback()
        raise
    for handle in staged:
        commit_staged_outputs(handle, job_id, "upgrade_workflow")
    delete_rerun_artifact_objects(service.object_store, deleted_rows, job_id, "upgrade_workflow")
