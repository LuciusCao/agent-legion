from __future__ import annotations

from typing import Any

from server.app.events import JobEventManager
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_workflow_upgrade_apply import apply_upgrade_once
from server.app.services.job_workflow_upgrade_gates import ActiveRevisionChangedError
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
    ) -> None:
        self.job_db = job_db
        self.lease_repo = lease_repo
        self.job_event_manager = job_event_manager
        self.job_event_buffer = job_event_buffer
        # #508 同款产物清理件（review P1-3）：重置闭包的本地产物暂存编排见
        # job_workflow_upgrade_staging（含 #759 复审 P1-A 的保护计划
        # fail-closed 闸），提交后收尾见 job_workflow_upgrade_cleanup。
        # None 时（裸构造的服务）退化为不做本地产物暂存，仅清单行清理。
        self.artifact_mutation = artifact_mutation
        self.object_store = object_store
        # P1-1（codex 四轮）：实现身份解析的 gate，与 dispatch 侧
        # ``workflows.custom_nodes_enabled`` 同源；关闭时 code 节点实现
        # 全部占位（保守重跑）。
        self.custom_nodes_enabled = custom_nodes_enabled

    def upgrade(self, workspace_id: str, job_id: str, *, mode: str = "clean") -> dict[str, Any]:
        if mode not in UPGRADE_MODES:
            raise ValueError(f"Unknown upgrade mode: {mode!r}")
        # #759 4.4 TOCTOU：guard 事务内重读 active revision 与 context 不符
        # 时整体重试一次（重解 context + 重 plan + 重进事务，全部输入来自
        # 同一份新 context.active，禁止半应用）；第二次仍不符以冲突结果返回
        # （与 JobMutationConflict 的 skipped 形态同款，reason_code 区分）。
        for attempt in range(2):
            try:
                return apply_upgrade_once(self, workspace_id, job_id, mode=mode)
            except ActiveRevisionChangedError:
                if attempt:
                    return upgrade_result(
                        job_id,
                        "skipped",
                        "revision_changed",
                        "Active workflow revision changed during upgrade",
                        mode=mode,
                    )
        raise AssertionError("unreachable: retry loop returns or raises")
