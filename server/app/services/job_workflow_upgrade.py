from __future__ import annotations

from typing import Any

from server.app.events import JobEventManager
from server.app.events.aggregator import broadcast_job_update, record_job_update
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.jobs.atomic_mutations import JobMutationConflict
from server.app.jobs.workflow_upgrade_mutation_inherit import upgrade_job_workflow_inherit
from server.app.services.job_artifact_mutation import JobArtifactMutationService, StagedOutputs
from server.app.services.job_workflow_upgrade_cleanup import (
    finalize_upgrade_staged_outputs,
    rollback_upgrade_staged_outputs,
)
from server.app.services.job_workflow_upgrade_gates import UpgradeContext, resolve_upgrade_context
from server.app.services.job_workflow_upgrade_impl import implementation_excluded_nodes
from server.app.services.job_workflow_upgrade_plan import plan_inherit_nodes
from server.app.services.job_workflow_upgrade_propagation import rerun_closure
from server.app.services.job_workflow_upgrade_removed_outputs import unprotected_input_names
from server.app.services.job_workflow_upgrade_result import upgrade_result

UPGRADE_MODES = ("clean", "inherit")


class JobWorkflowUpgradeService:
    def __init__(
        self,
        job_db: JobQueries,
        lease_repo: ExecutorLeaseRepository,
        job_event_manager: JobEventManager | None = None,
        job_event_buffer: Any | None = None,
        artifact_mutation: JobArtifactMutationService | None = None,
        object_store: Any = None,
        custom_nodes_enabled: bool = True,
        skill_manager: Any = None,
    ) -> None:
        self.job_db = job_db
        self.lease_repo = lease_repo
        self.job_event_manager = job_event_manager
        self.job_event_buffer = job_event_buffer
        # #508 同款产物清理件（review P1-3）：重置闭包的本地产物暂存、
        # 清单行同事务删除与提交后对象清理的编排见
        # job_workflow_upgrade_cleanup。None 时（裸构造的服务）退化为
        # 不做本地产物暂存，仅清单行清理。
        self.artifact_mutation = artifact_mutation
        self.object_store = object_store
        # P1-1（codex 四轮）：实现身份解析的 gate，与 dispatch 侧
        # ``workflows.custom_nodes_enabled`` 同源；关闭时 code 节点实现
        # 全部占位（保守重跑）。
        self.custom_nodes_enabled = custom_nodes_enabled
        # codex 五轮 P1-A：skill 内容身份比较的解析器（latest=live HEAD /
        # tag=DB 锁，与 dispatch 的 AgentDispatchService 同源装配）。
        # None 时（裸构造）agent 节点按 skill 不可证明保守重跑。
        self.skill_manager = skill_manager

    def upgrade(self, workspace_id: str, job_id: str, *, mode: str = "clean") -> dict[str, Any]:
        if mode not in UPGRADE_MODES:
            raise ValueError(f"Unknown upgrade mode: {mode!r}")
        context = resolve_upgrade_context(
            self.job_db, self.lease_repo, workspace_id, job_id, mode=mode
        )
        if not isinstance(context, UpgradeContext):
            return context

        # inherit 模式的继承集在事务外规划（读路径，纯函数见
        # job_workflow_upgrade_plan）；校验备妥后才进入统一应用。
        inherit_nodes: frozenset[str] = frozenset()
        if mode == "inherit":
            inherit_nodes = plan_inherit_nodes(
                self.job_db,
                context.job,
                context.definition,
                context.frozen_config_json,
                custom_nodes_enabled=self.custom_nodes_enabled,
                skill_manager=self.skill_manager,
            )
        staged: StagedOutputs | None = None
        try:
            # The intake batch's node_code_versions deliberately stay frozen:
            # the batch payload is shared by every job in the batch. Since
            # #115 ordinary jobs dispatch the latest published code anyway;
            # the frozen pins only matter to quality-replay batches.
            with self.job_db.lease_guarded_mutation(
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
                if inherit_nodes:
                    revalidated = implementation_excluded_nodes(
                        self.job_db,
                        context.job,
                        context.definition,
                        custom_nodes_enabled=self.custom_nodes_enabled,
                        skill_manager=self.skill_manager,
                    )
                    if revalidated:
                        # 重验得到的是新的变更种子，不只是要从 keep 集剔除
                        # 的孤立节点：实现漂移会使全部下游结果失效，同名
                        # 生产者也必须留在重置边界的同一侧。
                        inherit_nodes -= frozenset(
                            rerun_closure(context.definition, set(revalidated))
                        )
                inherit_nodes, staged = self.job_db.stage_upgrade_reset_outputs_in_transaction(
                    conn,
                    self.artifact_mutation,
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
                    # 全量清单清理输入——新图中「有声明输入面但无生产者」的
                    # 名字（RMW 名 + 外部输入）受保护：删行会让
                    # restore_missing_inputs 无清单可回、节点永久等输入
                    # （#114 语义）；有生产者的输入名不受保护（生产者重跑
                    # 重新产出，rename 场景的旧 key 行仍被清理）。裸构造
                    # 服务（无 artifact_mutation）维持旧行为的安全子集
                    # （不做全量清单清理）。
                    keep_input_names=unprotected_input_names(context.definition),
                    full_manifest_cleanup=self.artifact_mutation is not None,
                )
        except JobMutationConflict as exc:
            rollback_upgrade_staged_outputs(staged)
            return upgrade_result(job_id, "skipped", exc.reason_code, str(exc), mode=mode)
        except BaseException:
            # #204 broad-except audit: upgrade 的暂存件安全网（与
            # job_execution.run_to / job_rerun.commit_rerun 同款）。事务
            # 冲突臂已在上面剥离；这里兜住其余一切失败（DB 断连、产物
            # 清理缺陷等），先把已移出原位的本地产物回滚复位再原样上抛
            # ——否则节点产物会从 job_dir 消失而 DB 仍标记存在。
            rollback_upgrade_staged_outputs(staged)
            raise

        finalize_upgrade_staged_outputs(staged, self.object_store, stats["deleted_rows"], job_id)
        if self.job_event_buffer is not None:
            record_job_update(self.job_db, self.job_event_buffer, job_id)
        elif self.job_event_manager is not None:
            broadcast_job_update(self.job_db, self.job_event_manager, job_id)
        return upgrade_result(
            job_id,
            "succeeded",
            mode=mode,
            kept=stats["kept"],
            rerun=stats["rerun"],
        )
