"""upgrade-workflow 重置闭包的产物清理编排（issue #645 review P1-3）。

与 ``job_rerun`` / ``job_execution.run_to`` 的 ``#508`` 清理三件套同款：
事务内可逆暂存重置闭包的本地产物 → ``job_artifacts`` 清单行在同一
事务内删除（mutation 层）→ 提交后 ``commit`` 暂存件（彻底删除）+
对象存储 best-effort 删除。本模块只编排放置顺序与回滚安全网，清单行
删除本身在 ``workflow_upgrade_mutation_inherit`` 的事务里。

codex P1-1/P1-2/P1-3：``stage_upgrade_reset_outputs`` 由
``JobQueries.stage_upgrade_reset_outputs_in_transaction``（queries 门面，
issue #645）在 lease guard 事务内调用，返回 ``(暂存件, 实际继承集)``
——事务外先移文件时，queued job 在 resolve_upgrade_context 与事务之间
的窗口被调度器抢到（active lease / running node 由 guard 事务内复检拦
截），执行中节点会读到缺失文件或写进暂存路径（rerun / run_to 同款模
式：先入 guard 事务再动文件）。暂存面与继承集都按**事务内实际保留集**
收敛，而不是事务外的 diff 候选集：

- P1-2：未变候选若处于 failed/pending 且遗留部分输出，diff 集不会把它
  并进暂存，但 mutation 会把它重置 pending——旧文件与清单行残留会让
  executor 的输出存在性检查把上次失败/中断的半成品当作本次有效输出。
- P1-3：保留集不得与实际重置面共享输出名（含 RMW；对象键 ``jobs/<ws>/<job>/
  <name>`` 不含 node 身份，跨闭包重名只能一起重跑）——plan 阶段已按
  diff 重置面做过确定性排除，这里覆盖「未完成候选并入重置面」的组合
  场景与 plan/事务间的状态漂移。
- P1-2（codex 四轮）：旧快照中被移除的 output 名与被删节点的产物不
  留残——新 definition 的暂存名之外，旧快照侧「重置节点的被移除纯
  输出名 + 被删节点的全部纯输出名与 runs 目录」一并暂存/清清单行
  （``job_workflow_upgrade_removed_outputs`` 纯函数算面，A3 口径过滤：
  保留节点声明的名字不碰）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from server.app.services.job_artifact_mutation import JobArtifactMutationService, StagedOutputs
from server.app.services.job_staged_cleanup import (
    commit_staged_outputs,
    delete_rerun_artifact_objects,
)
from server.app.services.job_workflow_upgrade_propagation import rerun_closure
from server.app.services.job_workflow_upgrade_removed_outputs import removed_artifact_face

if TYPE_CHECKING:
    from server.app.workflows.definition import WorkflowDefinition

logger = logging.getLogger(__name__)


def stage_upgrade_reset_outputs(
    artifact_mutation: JobArtifactMutationService | None,
    job: dict[str, Any],
    definition: WorkflowDefinition,
    inherit_nodes: frozenset[str],
    existing_node_statuses: dict[str, Any],
) -> tuple[frozenset[str], StagedOutputs | None]:
    """事务内暂存重置闭包的本地输出，返回 ``(实际继承集, 暂存件)``。

    只在 ``lease_guarded_mutation`` 的事务内调用（``existing_node_statuses``
    是调用门面在同一事务内读到的 ``job_nodes`` 状态，BOUNDARY-DATA-001）：
    实际继承集 = 候选 ∩ 当前 completed，再剔除与实际重置面共享输出名（含 RMW）
    的候选（codex P1-3，含下游闭包，见模块 docstring）；调用方应把返回
    的继承集传给 mutation，而非事务外的 diff 候选集。服务未装配
    ``artifact_mutation``（裸构造）时跳过本地暂存——清单行清理仍生效，
    本地旧文件由节点重跑自然覆盖（旧行为的安全子集）。RMW 产物（同时
    是输入与输出）不在 ``stage_outputs`` 的暂存面内，与 rerun 语义一致
    （#114）。
    """
    keep_keys = {key for key in inherit_nodes if existing_node_statuses.get(key) == "completed"}
    # 事务内新出现的 reset 节点（例如计划时是候选、应用时已 failed）与
    # plan/revalidation 的种子同权：必须重新走统一的下游 + 同名生产者闭包。
    # 只做 shared-name 收敛会错误保留该节点的 completed 下游。
    reset_face = set(definition.executable_nodes) - keep_keys
    keep_keys -= rerun_closure(definition, reset_face)
    reset_keys = [key for key in definition.executable_nodes if key not in keep_keys]
    if artifact_mutation is None:
        return frozenset(keep_keys), None
    # codex 四轮 P1-2：旧快照（事务前 job 快照仍是旧 revision）补出被移除
    # output 名与被删节点面；无 artifact_mutation 时清单清理本就不生效
    # （裸构造的安全子集，与上方 None 臂一致），有则进暂存面与 artifact_names。
    from server.app.services.workflow_revision_format import definition_from_job_snapshot

    removed = removed_artifact_face(
        definition_from_job_snapshot(job), definition, frozenset(keep_keys), frozenset(reset_keys)
    )
    # codex 四轮复审 CRITICAL-1：被删节点的清理独立于重置面——只删终端
    # 节点、其余全继承时 reset_keys 为空，旧 guard ``not reset_keys`` 直接
    # 早退，被删节点的文件 / runs 目录 / 清单行全部遗留。removed 非空时
    # 即使 reset_keys 为空也走 stage_outputs：staging_output_names(∅) 为
    # 空、移动面只含 extra，安全。
    if not reset_keys and not removed:
        return frozenset(keep_keys), None
    # closure=reset_keys：不沿下游再扩散。rerun 语义的下游闭包对 upgrade
    # 不成立——实际重置面（P1-2 的未完成候选并入后）可能不是下游封闭的，
    # 保留节点的产物与运行历史不许被重置节点的下游传播顺带暂存。
    return frozenset(keep_keys), artifact_mutation.stage_outputs(
        job,
        reset_keys,
        definition,
        closure=frozenset(reset_keys),
        extra_names=removed.names,
        extra_run_keys=removed.run_keys,
    )


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
