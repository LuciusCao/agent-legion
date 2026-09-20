"""upgrade-workflow 的 conn 作用域产物暂存门面（issue #645 codex P1-1）。

``JobWorkflowUpgradeService.upgrade`` 的产物暂存必须发生在
``lease_guarded_mutation`` 事务内（与 ``mark_nodes_for_rerun_in_transaction``
同款通道）：暂存面与实际继承集都按事务内 ``job_nodes`` 状态收敛，编排
本体在 ``services/job_workflow_upgrade_staging``——本 mixin 负责在同一
事务内读取节点状态（``existing_node_states``，数据层）并把 conn 作用域
入口挂到 JobQueries 门面上，满足 BOUNDARY-DATA-001（service 层不直接
持有 conn-scoped 状态读取）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.jobs.workflow_upgrade_artifact_rows import existing_node_states

if TYPE_CHECKING:
    from server.app.db.connection import DatabaseConnection
    from server.app.services.job_artifact_mutation import (
        JobArtifactMutationService,
        StagedOutputs,
    )
    from server.app.services.job_workflow_upgrade_protection import InputProtectionPlan
    from server.app.workflows.definition import WorkflowDefinition


class UpgradeStagingQueriesMixin(ConnectionQueriesMixin):
    def stage_upgrade_reset_outputs_in_transaction(
        self,
        conn: DatabaseConnection,
        artifact_mutation: JobArtifactMutationService | None,
        job: dict[str, Any],
        definition: WorkflowDefinition,
        inherit_nodes: frozenset[str],
    ) -> tuple[frozenset[str], StagedOutputs | None, InputProtectionPlan]:
        """upgrade 事务内的重置闭包产物暂存（#645 codex P1-1/P1-2/P1-3）。

        返回 ``(实际继承集, 暂存件, 输入保护计划)``：继承集 = 候选 ∩ 事务内
        completed，再剔除与实际重置面共享输出名（含 RMW）的候选及其下游
        （对象键不含 node 身份，跨闭包重名只能一起重跑）。保护计划
        （#759 复审 P1-A）在同一收敛面上先于暂存计算，unprovable 非空时
        抛 ``UpgradeProtectionUnprovableError`` fail closed（零副作用）。
        详见 cleanup 模块 docstring。
        """
        from server.app.services.job_workflow_upgrade_staging import (
            stage_upgrade_reset_outputs,
        )

        return stage_upgrade_reset_outputs(
            artifact_mutation,
            job,
            definition,
            inherit_nodes,
            existing_node_states(conn, str(job["id"])),
        )
