"""clean 升级的两段产物暂存（#759 预算拆分自 ``job_workflow_upgrade_staging``）。

失效判定**按名**而非按节点对（#759 自审）：dropped = 旧定义全部
output − 新定义全部 output − 新定义全部消费名（统一索引键集，含分支
条件产物——`revision_diff.dropped_artifact_names`）——旧产出若在新定
义仍被消费（含跨节点转移、新 RMW 名、分支条件种子）就是种子而非垃圾。
旧产物名的存亡由此闭包**唯一**判定：第一段 extra_names 已覆盖全部
死名（含被删节点的，dropped 遍历旧定义全节点）；第二段对被删节点
只清 run history，不再按旧定义重枚举 outputs——被删生产者的产物
若已转移为新定义的输入，按节点枚举会把种子误暂存、清单行删除后
消费者永久无法 ready（#759 codex P1）。清单行删除与暂存集严格互补
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
    """暂存新定义可执行节点的产物 + 死名 + 被删节点的 run history。

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
                    include_outputs=False,
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
