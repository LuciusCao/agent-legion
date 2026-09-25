"""rework 决策的 staged 写路径（#759 预算拆分自 ``approval_rework``）。

一个 guarded transaction 同时提交审计行与节点重置：锁内重查 failed-
upstream（含 stale 集隐式生产者）→ 暂存 → 写 feedback（暂存已扫完、
提交前就位，两种旧竞态都不存在）→ 决策行 → 节点重置；失败整体回滚。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from server.app.jobs.atomic_mutations import JobMutationConflict
from server.app.jobs.queries.approval_decisions import ApprovalGateConflict
from server.app.scheduler_wakeup import notify_schedulable_work
from server.app.services.job_errors import ConflictError, InvalidOperationError
from server.app.services.job_operation_error import JobOperationError
from server.app.services.job_rerun.upstream_guard import raise_if_failed_upstream_in_tx
from server.app.services.job_reset_closure import rerun_reset_closure
from server.app.services.job_staged_cleanup import (
    commit_staged_outputs,
    delete_rerun_artifact_objects,
)

if TYPE_CHECKING:
    from server.app.services.approval_decisions import ApprovalDecisionService

logger = logging.getLogger(__name__)


def commit_rework(
    service: ApprovalDecisionService,
    job: dict[str, Any],
    definition: Any,
    node_key: str,
    target: str,
    decision: dict[str, Any],
    feedback_name: str,
    note: str,
    round_no: int,
    decided_by: str,
) -> dict[str, Any]:
    job_id = str(job["id"])
    # #759: 与 rerun 同一合并下游口径（显式边 ∪ 隐式消费边）；暂存集合
    # 与重置集合同源（stage_outputs 不做任何图遍历）。codex #776 复审 P1：
    # 同名纯输出生产者一并进重置面（rerun_reset_closure 统一收敛）。
    affected = sorted(rerun_reset_closure(definition, [target]))
    stale_nodes = [key for key in affected if key != target]
    staged = None
    deleted_rows: list[dict[str, Any]] = []
    try:
        with service.job_db.lease_guarded_mutation(
            job_id, datetime.now(UTC), reject_running_nodes=True
        ) as conn:
            # #759 invariant 5：failed-upstream 资格在锁内用当前状态重查。
            raise_if_failed_upstream_in_tx(
                service.job_db,
                conn,
                definition,
                target,
                job_id,
                "rework",
                target,
                stale_nodes=stale_nodes,
            )
            staged = service.rerun.artifact_service.stage_outputs(job, affected, definition)
            service._write_job_artifact(
                job,
                feedback_name,
                {
                    "gate": node_key,
                    "verdict": "rework",
                    "note": note,
                    "round": round_no,
                    "rework_target": target,
                    "decided_by": decided_by,
                    "decided_at": datetime.now(UTC).isoformat(),
                },
            )
            service.job_db.record_rework_decision_in_transaction(conn, decision)
            deleted_rows = service.job_db.mark_nodes_for_rerun_in_transaction(
                conn,
                job_id,
                [target],
                {target: stale_nodes},
                staged_artifact_names=staged.artifact_names,
            )
    except (ApprovalGateConflict, JobMutationConflict) as exc:
        if staged is not None:
            staged.rollback()
        raise ConflictError(str(exc)) from exc
    except JobOperationError as exc:
        # 锁内 failed-upstream 重查的业务拒绝：回滚暂存，归一为冲突。
        if staged is not None:
            staged.rollback()
        raise ConflictError(str(exc)) from exc
    except ValueError as exc:
        if staged is not None:
            staged.rollback()
        raise InvalidOperationError(str(exc)) from exc
    except Exception:
        # #204 broad-except audit: terminal safety net of the staged rework
        # mutation, mirroring commit_rerun / run_to / upgrade_staging. The
        # conflict arm (→ ConflictError) and the contract arm (ValueError →
        # InvalidOperationError) are handled above; this arm exists so the
        # staged artifacts (already moved off their original paths) are
        # ALWAYS rolled back before the error escapes — otherwise outputs
        # vanish from the job dir while the DB still marks them present.
        logger.exception("Failed to persist rework decision for job %s", job_id)
        if staged is not None:
            staged.rollback()
        raise
    commit_staged_outputs(staged, job_id, "rework")
    delete_rerun_artifact_objects(service.object_store, deleted_rows, job_id, "rework")
    service._upload_artifact(job, node_key, feedback_name)
    notify_schedulable_work()
    service._broadcast(job_id)
    return decision
