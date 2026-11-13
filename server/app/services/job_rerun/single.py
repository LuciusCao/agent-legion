from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.services.job_operation_error import JobOperationError, JobOperationResult
from server.app.services.job_rerun.commit import commit_rerun as _commit_rerun
from server.app.services.job_rerun.eligibility import check_rerun_eligibility
from server.app.services.workflow_definitions import require_workspace_active_definition
from server.app.services.workflow_revision_format import definition_from_job_snapshot

if TYPE_CHECKING:
    from server.app.services.job_rerun import JobRerunService


def resolve_rerun_node(
    job_db: Any,
    job_id: str,
    job: dict[str, Any],
    node_key: str | None,
    from_failed_node: bool,
) -> str:
    """Resolve the actual node key for a rerun, raising on an invalid selection."""
    if from_failed_node:
        if job.get("status") != "failed":
            raise JobOperationError(
                job_id, "rerun", "skipped", None, "not_failed", "Job is not failed"
            )
        for node in job_db.list_job_nodes(job_id):
            if node["status"] == "failed":
                return str(node["node_key"])
        raise JobOperationError(
            job_id, "rerun", "skipped", None, "no_failed_node", "No failed node found"
        )
    if node_key is None:
        raise JobOperationError(
            job_id, "rerun", "failed", None, "node_key_required", "node_key is required"
        )
    return node_key


def execute_rerun(
    service: JobRerunService,
    job: dict[str, Any],
    job_id: str,
    actual_node_key: str,
) -> JobOperationResult:
    """Validate and mark a single node for rerun."""
    ineligible = check_rerun_eligibility(service, job, job_id, actual_node_key)
    if ineligible is not None:
        raise ineligible
    return commit_rerun(service, job, job_id, actual_node_key)


def commit_rerun(
    service: JobRerunService,
    job: dict[str, Any],
    job_id: str,
    actual_node_key: str,
    *,
    definition: Any | None = None,
) -> JobOperationResult:
    """Write portion of ``execute_rerun`` with the ordinary unconstrained path."""
    if definition is None:
        definition = definition_from_job_snapshot(job) or require_workspace_active_definition(
            service.job_db, str(job["workspace_id"]), str(job["workspace_id"])
        )
    return _commit_rerun(service, job, job_id, actual_node_key, definition=definition)


def execute_rerun_result(
    service: JobRerunService,
    job: dict[str, Any],
    job_id: str,
    actual_node_key: str,
) -> JobOperationResult:
    """Rerun one node, capturing a non-succeeded outcome as a result dict (batch use)."""
    try:
        return execute_rerun(service, job, job_id, actual_node_key)
    except JobOperationError as exc:
        return exc.to_result()


def commit_rerun_result(
    service: JobRerunService,
    job: dict[str, Any],
    job_id: str,
    actual_node_key: str,
    *,
    definition: Any | None = None,
) -> JobOperationResult:
    """commit_rerun with the same error capture as ``execute_rerun_result``."""
    try:
        return commit_rerun(service, job, job_id, actual_node_key, definition=definition)
    except JobOperationError as exc:
        return exc.to_result()
