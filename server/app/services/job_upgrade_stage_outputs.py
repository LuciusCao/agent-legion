"""clean 升级的两段产物暂存（#759 预算拆分自 ``job_workflow_upgrade_staging``）。

失效判定**按名**而非按节点对（#759 自审）：dropped = 旧定义全部
output − 新定义全部 output − 新定义全部 input——旧产出若在新定义
仍被任一节点消费（含跨节点转移、新 RMW 名）就是种子而非垃圾。
被删节点的 RMW 名经 extra_names 强制暂存（节点已消失，#114 的死等
理由不成立，三者全失效才一致）。清单行删除与暂存集严格互补
（``upgrade_job_workflow`` 按暂存名删除，同 rerun）。
"""

from __future__ import annotations

import logging
from typing import Any

from server.app.services.job_artifact_mutation import JobArtifactMutationService, StagedOutputs
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.revision_diff import (
    dropped_artifact_names,
    removed_node_keys,
    removed_rmw_names,
)

logger = logging.getLogger(__name__)


def rollback_quietly(handle: StagedOutputs) -> None:
    """per-item 兜住的回滚：单个 handle 失败不中断其余补偿。"""
    try:
        handle.rollback()
    except OSError:
        logger.warning("staged rollback failed; residue left in .staged", exc_info=True)


def stage_upgrade_outputs(
    artifact_service: JobArtifactMutationService,
    job: dict[str, Any],
    new_definition: WorkflowDefinition,
    old_definition: WorkflowDefinition | None,
) -> list[StagedOutputs]:
    """暂存新旧定义可执行节点之并的产物；每个 handle 独立 commit/rollback。

    第二段及以后失败时回滚已收集的 handle，不允许半程丢失。
    """
    staged: list[StagedOutputs] = []
    try:
        dropped_names: set[str] = set()
        removed: list[str] = []
        if old_definition is not None:
            dropped_names = dropped_artifact_names(new_definition, old_definition)
            removed = removed_node_keys(new_definition, old_definition)
        staged.append(
            artifact_service.stage_outputs(
                job,
                sorted(new_definition.executable_nodes),
                new_definition,
                extra_names=sorted(dropped_names),
            )
        )
        if removed:
            assert old_definition is not None  # removed 只在 old_definition 分支计算
            staged.append(
                artifact_service.stage_outputs(
                    job,
                    removed,
                    old_definition,
                    extra_names=removed_rmw_names(old_definition, removed),
                )
            )
    except Exception:
        # #204 broad-except audit: 两段暂存的组合回滚——第二段及以后失败
        # （OSError/ValueError 来自 stage_outputs 的 fs 移动与路径校验）时
        # 已收集 handle 的产物必须全部回到原位，否则 DB 未变而第一批文件
        # 滞留 .staged（#759 自审 P1）。原异常类型原样上抛给
        # execute_staged_upgrade 的分类臂。
        for handle in staged:
            rollback_quietly(handle)
        raise
    return staged
