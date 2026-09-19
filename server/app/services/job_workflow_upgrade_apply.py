"""upgrade-workflow 的单次应用尝试（#759 4.4 的重试单元；文件预算拆分）。

一次尝试 = ``resolve_upgrade_context`` → ``plan_inherit_nodes`` →
lease guard 事务 → 提交后收尾。guard 事务**首步**重读 workspace 当前
active revision（``assert_context_revision_current``）：revision 发布
不经 job-mutation 锁，plan 与应用之间的发布（TOCTOU）只能靠事务内
重读兜底——与 ``context.active`` 不符即抛 ``ActiveRevisionChangedError``
整个尝试作废，service 层整体重试一次（重解 context + 重 plan + 重进
事务；plan、frozen config、继承集全部来自同一份新 context.active，
禁止半应用状态）。重读先于任何产物暂存与写操作，作废的尝试零副作用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.events.aggregator import broadcast_job_update, record_job_update
from server.app.jobs.atomic_mutations import JobMutationConflict
from server.app.jobs.workflow_upgrade_mutation_inherit import upgrade_job_workflow_inherit
from server.app.services.job_artifact_mutation import StagedOutputs
from server.app.services.job_workflow_upgrade_cleanup import (
    finalize_upgrade_staged_outputs,
    rollback_upgrade_staged_outputs,
)
from server.app.services.job_workflow_upgrade_gates import (
    UpgradeContext,
    assert_context_revision_current,
    resolve_upgrade_context,
)
from server.app.services.job_workflow_upgrade_impl import implementation_excluded_nodes
from server.app.services.job_workflow_upgrade_plan import plan_inherit_nodes
from server.app.services.job_workflow_upgrade_propagation import rerun_closure
from server.app.services.job_workflow_upgrade_removed_outputs import (
    deleted_node_keys,
    unprotected_input_names,
)
from server.app.services.job_workflow_upgrade_result import upgrade_result
from server.app.workflows.revision_format import definition_from_job_snapshot

if TYPE_CHECKING:
    from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService


def apply_upgrade_once(
    service: JobWorkflowUpgradeService, workspace_id: str, job_id: str, *, mode: str
) -> dict[str, Any]:
    """单次升级尝试；``ActiveRevisionChangedError`` 由调用方决定整体重试。"""
    context = resolve_upgrade_context(
        service.job_db, service.lease_repo, workspace_id, job_id, mode=mode
    )
    if not isinstance(context, UpgradeContext):
        return context

    # inherit 模式的继承集在事务外规划（读路径，纯函数见
    # job_workflow_upgrade_plan）；校验备妥后才进入统一应用。
    inherit_nodes: frozenset[str] = frozenset()
    if mode == "inherit":
        inherit_nodes = plan_inherit_nodes(
            service.job_db,
            context.job,
            context.definition,
            context.frozen_config_json,
            custom_nodes_enabled=service.custom_nodes_enabled,
        )
    staged: StagedOutputs | None = None
    try:
        # The intake batch's node_code_versions deliberately stay frozen:
        # the batch payload is shared by every job in the batch. Since
        # #115 ordinary jobs dispatch the latest published code anyway;
        # the frozen pins only matter to quality-replay batches.
        with service.job_db.lease_guarded_mutation(
            job_id,
            context.now,
            reject_running_nodes=True,
        ) as conn:
            # codex P1-1：产物暂存必须在 lease guard 的事务内——事务外
            # 先移文件时，queued job 在 resolve_upgrade_context 与本处
            # 之间的窗口被调度器抢到（active lease/running node 由 guard
            # 事务内复检拦截），执行中节点会读到缺失文件或写进暂存路径。
            # rerun（job_rerun.single.commit_rerun）与 run_to（
            # job_execution._run_to_with_start）同款模式。返回的实际继承
            # 集按事务内节点状态收敛（P1-2 未完成候选并入重置面 + P1-3
            # 共享输出名（含 RMW）的候选一起重跑），mutation 消费它而非 diff 候选。
            # codex 五轮 P2-C：实现身份在同一防线内重验——plan 与本事务
            # 之间 Agent/node code/skill 锁被重新发布时，guard 只查
            # lease/running 不验 published 身份，事务会消费旧继承集
            # （旧实现产物冒充新实现）。重验与事务内收敛层
            # （keep ∩ completed + shared_name 复算）同款风格：漂移节点
            # 放弃继承（降级重跑），传播面（下游/同名）由收敛层接管。
            # #759 P1：重验只剩纯 DB 读 + 字符串比较——skill 面直读锁
            # 文档（绕 5s doc cache）、latest 恒定排除、upgrade 永不
            # pin/不跑 git 子进程，事务回滚不留 skill 面副作用。
            assert_context_revision_current(service.job_db, workspace_id, context)
            if inherit_nodes:
                # 与 Agent/node-code 的 publish/rollback/archive 共用
                # workspace 事务锁，重验到提交之间 published 身份不可变。
                service.job_db.acquire_implementation_publication_lock(conn, workspace_id)
                revalidated = implementation_excluded_nodes(
                    service.job_db,
                    context.job,
                    context.definition,
                    custom_nodes_enabled=service.custom_nodes_enabled,
                )
                if revalidated:
                    # 重验得到的是新的变更种子，不只是要从 keep 集剔除
                    # 的孤立节点：实现漂移会使全部下游结果失效，同名
                    # 生产者也必须留在重置边界的同一侧。
                    inherit_nodes -= frozenset(rerun_closure(context.definition, set(revalidated)))
            inherit_nodes, staged = service.job_db.stage_upgrade_reset_outputs_in_transaction(
                conn,
                service.artifact_mutation,
                context.job,
                context.definition,
                inherit_nodes,
            )
            stats = upgrade_job_workflow_inherit(
                conn,
                job_id,
                workflow_revision_id=str(context.active["id"]),
                workflow_version=int(context.active["version"]),
                workflow_definition_hash=str(context.active["definition_hash"]),
                workflow_definition_snapshot_json=str(context.active["definition_json"]),
                node_keys=list(context.definition.executable_nodes),
                frozen_config_json=context.frozen_config_json,
                inherit_nodes=inherit_nodes,
                staged_artifact_names=(
                    staged.artifact_names if staged is not None else frozenset()
                ),
                # codex 五轮 P2-D：clean 语义分支（无任何继承节点）的
                # 全量清单清理输入——新图中「无保证先行生产者」的输入名
                # （#759 4.1：RMW 启动名 + 外部输入）受保护：删行会让
                # hydration/restore_missing_inputs 无清单可回、节点永久
                # 等输入（#114 语义）。裸构造服务（无 artifact_mutation）
                # 维持旧行为的安全子集（不做全量清单清理）。
                keep_input_names=unprotected_input_names(context.definition),
                full_manifest_cleanup=service.artifact_mutation is not None,
                # #759 4.3：被删节点身份来自 old/new definition 差集（与
                # removed_artifact_face 的产物名/runs 目录面同源），
                # job_nodes 行缺失/多行的漂移场景口径一致。
                removed_node_keys=deleted_node_keys(
                    definition_from_job_snapshot(context.job), context.definition
                ),
            )
    except JobMutationConflict as exc:
        rollback_upgrade_staged_outputs(staged)
        return upgrade_result(job_id, "skipped", exc.reason_code, str(exc), mode=mode)
    except BaseException:
        # #204 broad-except audit: upgrade 的暂存件安全网（与
        # job_execution.run_to / job_rerun.commit_rerun 同款）。事务
        # 冲突臂已在上面剥离；这里兜住其余一切失败（DB 断连、产物
        # 清理缺陷、#759 4.4 的 ActiveRevisionChangedError——其触发点
        # 先于任何暂存，staged 必为 None），先把已移出原位的本地产物
        # 回滚复位再原样上抛——否则节点产物会从 job_dir 消失而 DB 仍
        # 标记存在。
        rollback_upgrade_staged_outputs(staged)
        raise

    finalize_upgrade_staged_outputs(staged, service.object_store, stats["deleted_rows"], job_id)
    if service.job_event_buffer is not None:
        record_job_update(service.job_db, service.job_event_buffer, job_id)
    elif service.job_event_manager is not None:
        broadcast_job_update(service.job_db, service.job_event_manager, job_id)
    return upgrade_result(
        job_id,
        "succeeded",
        mode=mode,
        kept=stats["kept"],
        rerun=stats["rerun"],
    )
