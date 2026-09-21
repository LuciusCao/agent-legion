"""Rework decision orchestration for approval gates (EXEC-APPROVAL-001).

Split from ``approval_decisions`` for the file-size budget. One guarded
transaction commits the audit row and the node reset together — a failed
reset must never leave a phantom rework decision behind (Codex P1 on #266).
Mirrors ``job_rerun.single.commit_rerun``'s shape: eligibility precheck →
staged output cleanup inside ``lease_guarded_mutation`` → commit or roll
everything back as one unit (write path in ``approval_rework_commit``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.services.approval_rework_commit import commit_rework
from server.app.services.job_errors import ConflictError, InvalidOperationError
from server.app.services.job_rerun.eligibility import check_rerun_eligibility
from server.app.workflows.approval_node import (
    AWAITING_APPROVAL_STATUS,
    approval_feedback_artifact,
    approval_rework_target,
)
from server.app.workflows.workflow_consumption import dependency_ancestors

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
    # #759：合并上游（显式边 ∪ 隐式生产边）——产物由隐式生产者产出的
    # gate 也必须能 rework 到真正的生产者。隐式边成环时 walk 会回到
    # 起点，必须像旧 ancestor_closure 一样排除自身。
    upstream = set(dependency_ancestors(definition, node_key)) - {node_key}
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
    # it. Written INSIDE the critical section right after stage_outputs
    # (#759 自审): staging has already swept, so the note can't be mistaken
    # for a stale output; and it lands before commit, so no dispatch can
    # observe the reworked target without the note (post-commit writes left
    # a stale/missing-read window; pre-staging writes got swept). A rolled
    # back rework leaves the note as a harmless stale file the next round
    # overwrites.
    return commit_rework(
        service,
        job,
        definition,
        node_key,
        target,
        decision,
        approval_feedback_artifact(node),
        note,
        round_no,
        decided_by,
    )
