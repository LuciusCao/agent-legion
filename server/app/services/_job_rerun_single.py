from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from server.app.events import broadcast_job_update, record_job_update
from server.app.jobs.atomic_mutations import JobMutationConflict
from server.app.services.job_staged_cleanup import commit_staged_outputs
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.workflows.scheduler import downstream_nodes

if TYPE_CHECKING:
    from server.app.services.job_rerun import JobRerunService

logger = logging.getLogger(__name__)


def resolve_rerun_node(
    job_db: Any,
    job_id: str,
    job: dict[str, Any],
    node_key: str | None,
    from_failed_node: bool,
) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve the actual node key for a rerun and return any early error."""
    if from_failed_node:
        if job.get("status") != "failed":
            return None, {
                "job_id": job_id,
                "operation": "rerun",
                "status": "skipped",
                "node_key": None,
                "reason_code": "not_failed",
                "message": "Job is not failed",
            }
        for node in job_db.list_job_nodes(job_id):
            if node["status"] == "failed":
                return str(node["node_key"]), None
        return None, {
            "job_id": job_id,
            "operation": "rerun",
            "status": "skipped",
            "node_key": None,
            "reason_code": "no_failed_node",
            "message": "No failed node found",
        }
    if node_key is None:
        return None, {
            "job_id": job_id,
            "operation": "rerun",
            "status": "failed",
            "node_key": None,
            "reason_code": "node_key_required",
            "message": "node_key is required",
        }
    return node_key, None


def execute_rerun(
    service: JobRerunService,
    job: dict[str, Any],
    job_id: str,
    actual_node_key: str,
) -> dict[str, Any]:
    """Validate and mark a single node for rerun."""
    definition = definition_from_job_snapshot(job) or service.workflows.definition(
        str(job["workflow_key"])
    )
    if actual_node_key not in definition.nodes:
        return _result(
            job_id,
            "failed",
            actual_node_key,
            "node_not_found",
            f"Node {actual_node_key} not found in workflow",
        )

    if service.job_db.get_job_node(job_id, actual_node_key) is None:
        return _result(
            job_id,
            "failed",
            actual_node_key,
            "node_not_found",
            f"Node {actual_node_key} not found for job",
        )

    if service.lease_repo.has_active_for_node(job_id, actual_node_key, service._now()):
        return _result(
            job_id,
            "skipped",
            actual_node_key,
            "busy",
            "Node has an active executor lease",
        )

    if service._job_has_running_nodes(job_id):
        return _result(
            job_id,
            "skipped",
            actual_node_key,
            "busy",
            "Job has running nodes",
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
        return _result(job_id, "skipped", actual_node_key, exc.reason_code, str(exc))
    except ValueError as exc:
        if staged is not None:
            staged.rollback()
        return _result(job_id, "failed", actual_node_key, "cleanup_failed", str(exc))
    except Exception as exc:
        logger.exception("Failed to mark nodes for rerun for job %s", job_id)
        if staged is not None:
            staged.rollback()
        return _result(
            job_id,
            "failed",
            actual_node_key,
            "rerun_failed",
            str(exc),
        )

    commit_staged_outputs(staged, job_id, "rerun")
    if service.job_event_buffer is not None:
        record_job_update(service.job_db, service.job_event_buffer, job_id)
    elif service.job_event_manager is not None:
        broadcast_job_update(service.job_db, service.job_event_manager, job_id)
    return _result(job_id, "succeeded", actual_node_key)


def _result(
    job_id: str,
    status: str,
    node_key: str | None = None,
    reason_code: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "operation": "rerun",
        "status": status,
        "node_key": node_key,
        "reason_code": reason_code,
        "message": message,
    }
