"""Rework decision orchestration for approval gates (EXEC-APPROVAL-001).

Split from ``approval_decisions`` for the file-size budget. One guarded
transaction commits the audit row and the node reset together — a failed
reset must never leave a phantom rework decision behind (Codex P1 on #266).
Mirrors ``job_rerun.single.commit_rerun``'s shape: eligibility precheck →
staged output cleanup inside ``lease_guarded_mutation`` → commit or roll
everything back as one unit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from server.app.jobs.atomic_mutations import JobMutationConflict
from server.app.jobs.queries.approval_decisions import ApprovalGateConflict
from server.app.scheduler_wakeup import notify_schedulable_work
from server.app.services.job_errors import ConflictError, InvalidOperationError
from server.app.services.job_rerun.eligibility import check_rerun_eligibility
from server.app.services.job_staged_cleanup import commit_staged_outputs
from server.app.workflows.approval_node import (
    AWAITING_APPROVAL_STATUS,
    approval_feedback_artifact,
    approval_rework_target,
)
from server.app.workflows.execution_control import ancestor_closure
from server.app.workflows.workflow_branching import downstream_nodes

if TYPE_CHECKING:
    from server.app.services.approval_decisions import ApprovalDecisionService


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
    upstream = ancestor_closure(definition, node_key) - {node_key}
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
    # it. Written before the transaction — a rolled-back rework leaves a
    # harmless stale file the next round overwrites.
    feedback_name = approval_feedback_artifact(node)
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
    # One guarded transaction commits the audit row and the node reset
    # together: staged output cleanup rolls back with the transaction.
    stale_nodes = downstream_nodes(definition, target)
    staged = None
    try:
        with service.job_db.lease_guarded_mutation(
            job_id, datetime.now(UTC), reject_running_nodes=True
        ) as conn:
            staged = service.rerun.artifact_service.stage_outputs(job, [target], definition)
            service.job_db.record_rework_decision_in_transaction(conn, decision)
            service.job_db.mark_nodes_for_rerun_in_transaction(
                conn, job_id, [target], {target: stale_nodes}
            )
    except (ApprovalGateConflict, JobMutationConflict) as exc:
        if staged is not None:
            staged.rollback()
        raise ConflictError(str(exc)) from exc
    except ValueError as exc:
        if staged is not None:
            staged.rollback()
        raise InvalidOperationError(str(exc)) from exc
    commit_staged_outputs(staged, job_id, "rework")
    service._upload_artifact(job, node_key, feedback_name)
    notify_schedulable_work()
    service._broadcast(job_id)
    return decision
