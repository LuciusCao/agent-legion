from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from server.app.events.aggregator import broadcast_job_update, record_job_update
from server.app.jobs.atomic_mutations import JobMutationConflict
from server.app.scheduler_wakeup import notify_schedulable_work
from server.app.services._job_rerun_eligibility import check_rerun_eligibility
from server.app.services.job_operation_error import JobOperationError, JobOperationResult
from server.app.services.job_staged_cleanup import commit_staged_outputs
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.workflows.workflow_branching import downstream_nodes

if TYPE_CHECKING:
    from server.app.services.job_rerun import JobRerunService

logger = logging.getLogger(__name__)


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

    definition = definition_from_job_snapshot(job) or service.workflows.definition(
        str(job["workflow_key"])
    )

    stale_nodes = downstream_nodes(definition, actual_node_key)
    staged = None
    try:
        with service.job_db.lease_guarded_mutation(
            job_id,
            service._now(),
            reject_running_nodes=True,
        ) as conn:
            staged = service.artifact_service.stage_outputs(job, [actual_node_key], definition)
            service.job_db.mark_nodes_for_rerun_in_transaction(
                conn, job_id, [actual_node_key], {actual_node_key: stale_nodes}
            )
    except JobMutationConflict as exc:
        if staged is not None:
            staged.rollback()
        raise JobOperationError(
            job_id, "rerun", "skipped", actual_node_key, exc.reason_code, str(exc)
        ) from exc
    except ValueError as exc:
        if staged is not None:
            staged.rollback()
        raise JobOperationError(
            job_id, "rerun", "failed", actual_node_key, "cleanup_failed", str(exc)
        ) from exc
    except Exception as exc:
        logger.exception("Failed to mark nodes for rerun for job %s", job_id)
        if staged is not None:
            staged.rollback()
        raise JobOperationError(
            job_id,
            "rerun",
            "failed",
            actual_node_key,
            "rerun_failed",
            str(exc),
        ) from exc

    commit_staged_outputs(staged, job_id, "rerun")
    notify_schedulable_work()
    if service.job_event_buffer is not None:
        record_job_update(service.job_db, service.job_event_buffer, job_id)
    elif service.job_event_manager is not None:
        broadcast_job_update(service.job_db, service.job_event_manager, job_id)
    return _result(job_id, "succeeded", actual_node_key)


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


def _result(job_id: str, status: str, node_key: str | None = None) -> JobOperationResult:
    return {
        "job_id": job_id,
        "operation": "rerun",
        "status": status,
        "node_key": node_key,
        "reason_code": None,
        "message": None,
    }
