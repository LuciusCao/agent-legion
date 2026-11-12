"""Guarded approve/reject commits, split from the approval service budget."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.scheduler_wakeup import notify_schedulable_work
from server.app.services.staged_json_artifact import replace_durable, stage_json

if TYPE_CHECKING:
    from server.app.services.approval_decisions import ApprovalDecisionService


def approve(
    service: ApprovalDecisionService,
    job: dict[str, Any],
    node_key: str,
    note: str,
    decided_by: str,
    payload: dict[str, Any],
    batch_id: str | None,
    batch_selection: str | None,
) -> dict[str, Any]:
    job_id = str(job["id"])
    decision = service._decision_row(
        job_id,
        node_key,
        "approved",
        note,
        "",
        decided_by,
        payload=payload,
        batch_id=batch_id,
        batch_selection=batch_selection,
    )
    # The staged artifact is swapped only after the in-transaction status and
    # batch guards pass; a failed swap or fsync rolls back the decision (#929/#975).
    artifact_name = f"{node_key}.approval.json"
    target = service._artifact_path(job, artifact_name)
    staged = stage_json(target, decision)
    try:
        service._gate_transition(
            lambda: service.job_db.approve_gate_atomic(
                decision, on_guarded=lambda: replace_durable(staged, target)
            )
        )
    finally:
        staged.unlink(missing_ok=True)
    service._upload_artifact(job, node_key, artifact_name)
    notify_schedulable_work()
    service._broadcast(job_id)
    return decision


def reject(
    service: ApprovalDecisionService,
    job: dict[str, Any],
    node_key: str,
    note: str,
    decided_by: str,
    batch_id: str | None,
    batch_selection: str | None,
) -> dict[str, Any]:
    job_id = str(job["id"])
    decision = service._decision_row(
        job_id,
        node_key,
        "rejected",
        note,
        "",
        decided_by,
        batch_id=batch_id,
        batch_selection=batch_selection,
    )
    message = f"rejected by reviewer: {note}" if note else "rejected by reviewer"
    service._gate_transition(lambda: service.job_db.reject_gate_atomic(decision, message))
    service._broadcast(job_id)
    return decision
