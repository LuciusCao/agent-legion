"""Per-job batch approval semantics (#1363)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.services.approval_payload import (
    approval_manual_input_fields,
    approval_payload_defaults,
)
from server.app.services.job_errors import ConflictError, NotFoundError
from server.app.services.workflow_definitions import require_workspace_active_definition
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.workflows.approval_node import APPROVAL_NODE_TYPE

if TYPE_CHECKING:
    from server.app.services.approval_decisions import ApprovalDecisionService


def _result(
    job_id: str,
    operation: str,
    status: str,
    *,
    node_key: str | None = None,
    reason_code: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "operation": operation,
        "status": status,
        "node_key": node_key,
        "reason_code": reason_code,
        "message": message,
    }


def batch_decide(
    service: ApprovalDecisionService,
    workspace_id: str,
    job_ids: list[str],
    *,
    verdict: str,
    note: str,
    decided_by: str,
    batch_id: str,
    batch_selection: str,
) -> list[dict[str, Any]]:
    operation = "approve" if verdict == "approved" else "reject"
    results: list[dict[str, Any]] = []
    ids = dict.fromkeys(value.strip() for value in job_ids if value.strip())
    for job_id in ids:
        try:
            job = service._require_job(workspace_id, job_id)
        except NotFoundError:
            results.append(
                _result(
                    job_id, operation, "failed", reason_code="not_found", message="Job not found"
                )
            )
            continue
        gate_keys = service.job_db.list_awaiting_approval_node_keys(job_id)
        if len(gate_keys) != 1:
            reason = "multiple_awaiting_gates" if gate_keys else "not_awaiting_approval"
            results.append(_result(job_id, operation, "skipped", reason_code=reason))
            continue
        node_key = gate_keys[0]
        definition = definition_from_job_snapshot(job) or require_workspace_active_definition(
            service.job_db, workspace_id, workspace_id
        )
        node = definition.nodes.get(node_key)
        if node is None or node.node_type != APPROVAL_NODE_TYPE:
            results.append(
                _result(
                    job_id,
                    operation,
                    "skipped",
                    node_key=node_key,
                    reason_code="gate_conflict",
                    message="Awaiting node is not an approval gate",
                )
            )
            continue
        if verdict == "approved" and approval_manual_input_fields(node):
            results.append(
                _result(
                    job_id,
                    operation,
                    "skipped",
                    node_key=node_key,
                    reason_code="needs_manual_input",
                )
            )
            continue
        try:
            service.decide(
                workspace_id,
                job_id,
                node_key,
                verdict=verdict,
                note=note,
                payload=approval_payload_defaults(node) if verdict == "approved" else {},
                decided_by=decided_by,
                batch_id=batch_id,
                batch_selection=batch_selection,
            )
        except (ConflictError, NotFoundError) as exc:
            results.append(
                _result(
                    job_id,
                    operation,
                    "skipped",
                    node_key=node_key,
                    reason_code="gate_conflict",
                    message=str(exc),
                )
            )
            continue
        results.append(_result(job_id, operation, "succeeded", node_key=node_key))
    return results
