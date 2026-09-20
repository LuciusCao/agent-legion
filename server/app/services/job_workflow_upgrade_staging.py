<<<<<<< HEAD
"""clean 升级的 staged 突变编排（#759 预算拆分自 ``job_workflow_upgrade``）。

mutation 锁内暂存 + 切换 revision + 提交后清理；清单行按暂存名精确删除
（同 rerun）——「保留 ⇔ 未暂存」构造性成立，无独立 preserve 集。
=======
"""upgrade-workflow 重置闭包的事务内产物暂存编排（issue #645 review P1-3）。

与 ``job_rerun`` / ``job_execution.run_to`` 的 ``#508`` 清理同款：事务内
可逆暂存重置闭包的本地产物 → ``job_artifacts`` 清单行在同一事务内删除
（mutation 层）→ 提交后收尾见 ``job_workflow_upgrade_cleanup``（文件预算
拆分）。本函数由 ``JobQueries.stage_upgrade_reset_outputs_in_transaction``
（queries 门面）在 lease guard 事务内调用，返回 ``(实际继承集, 暂存件,
输入保护计划)``——事务外先移文件时，queued job 在 resolve_upgrade_context
与事务之间的窗口被调度器抢到（active lease / running 节点由 guard 事务内
复检拦截），执行中节点会读到缺失文件或写进暂存路径（rerun / run_to 同款
模式：先入 guard 事务再动文件）。暂存面与继承集都按**事务内实际保留集**
收敛，而不是事务外的 diff 候选集：

- P1-2：未变候选若处于 failed/pending 且遗留部分输出，diff 集不会把它
  并进暂存，但 mutation 会把它重置 pending——旧文件与清单行残留会让
  executor 的输出存在性检查把上次失败/中断的半成品当作本次有效输出。
- P1-3：保留集不得与实际重置面共享输出名（含 RMW；对象键 ``jobs/<ws>/<job>/
  <name>`` 不含 node 身份，跨闭包重名只能一起重跑）——plan 阶段已按
  diff 重置面做过确定性排除，这里覆盖「未完成候选并入重置面」的组合
  场景与 plan/事务间的状态漂移。
- P1-2（codex 四轮）：旧快照中被移除的 output 名与被删节点的产物不留残
  （``job_workflow_upgrade_removed_outputs`` 纯函数算面，A3 口径过滤：
  保留节点声明的名字不碰）。
- #759 复审 P1-A：输入保护计划（``job_workflow_upgrade_protection``）在
  收敛后的保留/重置面上、**任何文件暂存之前**计算；unprovable 非空 ⇒
  抛 ``UpgradeProtectionUnprovableError`` fail closed（事务回滚，零副作用）。
  计划的 keep 集同时是 removed 面的 protected_names 与 clean/全退化分支
  全量清单清理的 keep_input_names。
>>>>>>> 3f038f6d7 (feat(jobs)：workflow 升级 inherit 模式全量——revision diff/实现身份/保护计划/cleanup + 发布锁域 #645 #759)
"""

from __future__ import annotations

<<<<<<< HEAD
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
=======
from typing import Any

from server.app.services.job_artifact_mutation import JobArtifactMutationService, StagedOutputs
from server.app.services.job_artifact_staging_scope import staging_output_names
from server.app.services.job_workflow_upgrade_propagation import rerun_closure
from server.app.services.job_workflow_upgrade_protection import (
    InputProtectionPlan,
    UpgradeProtectionUnprovableError,
    input_protection_plan,
)
from server.app.services.job_workflow_upgrade_removed_outputs import removed_artifact_face
from server.app.workflows.definition import WorkflowDefinition


def stage_upgrade_reset_outputs(
    artifact_mutation: JobArtifactMutationService | None,
    job: dict[str, Any],
    definition: WorkflowDefinition,
    inherit_nodes: frozenset[str],
    existing_node_statuses: dict[str, Any],
) -> tuple[frozenset[str], StagedOutputs | None, InputProtectionPlan]:
    """事务内暂存重置闭包的本地输出，返回 ``(实际继承集, 暂存件, 保护计划)``。

    只在 ``lease_guarded_mutation`` 的事务内调用（``existing_node_statuses``
    是调用门面在同一事务内读到的 ``job_nodes`` 状态，BOUNDARY-DATA-001）：
    实际继承集 = 候选 ∩ 当前 completed，再剔除与实际重置面共享输出名（含 RMW）
    的候选（codex P1-3，含下游闭包，见模块 docstring）；调用方应把返回
    的继承集传给 mutation，而非事务外的 diff 候选集。服务未装配
    ``artifact_mutation``（裸构造）时跳过本地暂存——清单行清理仍生效，
    本地旧文件由节点重跑自然覆盖（旧行为的安全子集）。RMW 产物（同时
    是输入与输出）不在 ``stage_outputs`` 的暂存面内，与 rerun 语义一致
    （#114）。

    保护计划（#759 复审 P1-A）在收敛后的保留/重置面上计算，先于一切
    文件暂存：unprovable 非空 ⇒ ``UpgradeProtectionUnprovableError``
    （fail closed，事务回滚零副作用——留多旧字节复活是静默错误、删多
    启动输入丢失是永久等待，两方向都不可证时不许猜）。
    """
    keep_keys = {key for key in inherit_nodes if existing_node_statuses.get(key) == "completed"}
    # 事务内新出现的 reset 节点（例如计划时是候选、应用时已 failed）与
    # plan/revalidation 的种子同权：必须重新走统一的下游 + 同名生产者闭包。
    # 只做 shared-name 收敛会错误保留该节点的 completed 下游。
    reset_face = set(definition.executable_nodes) - keep_keys
    keep_keys -= rerun_closure(definition, reset_face)
    reset_keys = [key for key in definition.executable_nodes if key not in keep_keys]
    protection = input_protection_plan(
        definition,
        keep_nodes=frozenset(keep_keys),
        reset_nodes=frozenset(reset_keys),
        staged_names=frozenset(staging_output_names(definition, set(reset_keys))),
    )
    if protection.unprovable:
        raise UpgradeProtectionUnprovableError(
            "input protection plan unprovable for names: "
            + ", ".join(sorted(protection.unprovable))
        )
    if artifact_mutation is None:
        return frozenset(keep_keys), None, protection
    # codex 四轮 P1-2：旧快照（事务前 job 快照仍是旧 revision）补出被移除
    # output 名与被删节点面；无 artifact_mutation 时清单清理本就不生效
    # （裸构造的安全子集，与上方 None 臂一致），有则进暂存面与 artifact_names。
    from server.app.services.workflow_revision_format import definition_from_job_snapshot

    removed = removed_artifact_face(
        definition_from_job_snapshot(job),
        definition,
        frozenset(keep_keys),
        frozenset(reset_keys),
        protected_names=protection.keep,
    )
    # codex 四轮复审 CRITICAL-1：被删节点的清理独立于重置面——只删终端
    # 节点、其余全继承时 reset_keys 为空，旧 guard ``not reset_keys`` 直接
    # 早退，被删节点的文件 / runs 目录 / 清单行全部遗留。removed 非空时
    # 即使 reset_keys 为空也走 stage_outputs：staging_output_names(∅) 为
    # 空、移动面只含 extra，安全。
    if not reset_keys and not removed:
        return frozenset(keep_keys), None, protection
    # closure=reset_keys：不沿下游再扩散。rerun 语义的下游闭包对 upgrade
    # 不成立——实际重置面（P1-2 的未完成候选并入后）可能不是下游封闭的，
    # 保留节点的产物与运行历史不许被重置节点的下游传播顺带暂存。
    staged = artifact_mutation.stage_outputs(
        job,
        reset_keys,
        definition,
        closure=frozenset(reset_keys),
        extra_names=removed.names,
        extra_run_keys=removed.run_keys,
    )
    return frozenset(keep_keys), staged, protection
>>>>>>> 3f038f6d7 (feat(jobs)：workflow 升级 inherit 模式全量——revision diff/实现身份/保护计划/cleanup + 发布锁域 #645 #759)
