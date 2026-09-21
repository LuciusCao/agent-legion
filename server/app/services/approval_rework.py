"""Rework decision orchestration for approval gates (EXEC-APPROVAL-001).

Split from ``approval_decisions`` for the file-size budget. One guarded
transaction commits the audit row and the node reset together — a failed
reset must never leave a phantom rework decision behind (Codex P1 on #266).
Mirrors ``job_rerun.single.commit_rerun``'s shape: eligibility precheck →
staged output cleanup inside ``lease_guarded_mutation`` → commit or roll
everything back as one unit.
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
from server.app.services.job_rerun.eligibility import check_rerun_eligibility
from server.app.services.job_rerun.upstream_guard import raise_if_failed_upstream_in_tx
from server.app.services.job_staged_cleanup import (
    commit_staged_outputs,
    delete_rerun_artifact_objects,
)
from server.app.workflows.approval_node import (
    AWAITING_APPROVAL_STATUS,
    approval_feedback_artifact,
    approval_rework_target,
)
from server.app.workflows.workflow_consumption import dependency_ancestors, dependency_downstream

if TYPE_CHECKING:
    from server.app.services.approval_decisions import ApprovalDecisionService

logger = logging.getLogger(__name__)


def execute_rework(
    service: ApprovalDecisionService,
    job: dict[str, Any],
    definition: Any,
    node: Any,
    note: str,
    rework_target: str,
    decided_by: str,
) -> dict[str, Any]:
    job_id = str(job["id"])
    node_key = node.key
    if not note.strip():
        raise InvalidOperationError("Rework requires a reviewer note (修改意见)")
    target = rework_target or approval_rework_target(node)
    if not target:
        raise InvalidOperationError(
            "Rework requires a target node: pass rework_target or declare"
            f" config.rework_target on approval node {node_key}"
        )
    # #759：合并上游（显式边 ∪ 隐式生产边）——产物由隐式生产者产出的
    # gate 也必须能 rework 到真正的生产者。
    upstream = dependency_ancestors(definition, node_key)
    eligible = {key for key in upstream if definition.nodes[key].node_type not in ("start",)}
    if target not in eligible:
        raise InvalidOperationError(
            f"Rework target {target!r} must be an upstream node of {node_key};"
            f" eligible: {sorted(eligible)}"
        )
    current = service.job_db.approval_gate_status(job_id, node_key)
    if current != AWAITING_APPROVAL_STATUS:
        raise ConflictError(
            f"Node {node_key} is not awaiting approval (status: {current or 'missing'})"
        )

    # Rerun eligibility runs before any write (busy leases, running nodes,
    # failed upstream) so an ineligible rework leaves nothing behind.
    ineligible = check_rerun_eligibility(service.rerun, job, job_id, target)
    if ineligible is not None:
        raise ConflictError(str(ineligible.failure_detail or ineligible))

    decision = service._decision_row(job_id, node_key, "rework", note, target, decided_by)
    round_no = service.job_db.count_approval_decisions(job_id, node_key) + 1
    # The feedback artifact is the reviewer's note as machine input: the
    # regenerating skill declares it as an optional input and rewrites with
    # it. Written AFTER the mutation commits (#759 自审): written before, a
    # workflow declaring the feedback name as some affected node's output
    # would have stage_outputs sweep the just-written note into staging and
    # delete it on commit; a rolled-back rework simply writes nothing.
    feedback_name = approval_feedback_artifact(node)
    # One guarded transaction commits the audit row and the node reset
    # together: staged output cleanup rolls back with the transaction.
    # #759: 与 rerun 同一合并下游口径（显式边 ∪ 隐式消费边）；暂存集合
    # 与重置集合同源（stage_outputs 不做任何图遍历）。
    stale_nodes = dependency_downstream(definition, target)
    affected = sorted({target, *stale_nodes})
    staged = None
    deleted_rows: list[dict[str, Any]] = []
    try:
        with service.job_db.lease_guarded_mutation(
            job_id, datetime.now(UTC), reject_running_nodes=True
        ) as conn:
            # #759 invariant 5：failed-upstream 资格在锁内用当前状态重查。
            raise_if_failed_upstream_in_tx(
                service.job_db, conn, definition, target, job_id, "rework", target
            )
            staged = service.rerun.artifact_service.stage_outputs(job, affected, definition)
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
    service._upload_artifact(job, node_key, feedback_name)
    notify_schedulable_work()
    service._broadcast(job_id)
    return decision
