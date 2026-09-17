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
from server.app.services.job_workflow_upgrade_plan import plan_inherit_nodes
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
                # 共享纯输出名的候选一起重跑），mutation 消费它而非 diff 候选。
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
