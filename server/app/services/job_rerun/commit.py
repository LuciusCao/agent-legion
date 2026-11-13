"""Single-transaction rerun commit, with an optional reviewed recovery guard."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from server.app.events.aggregator import broadcast_job_update, record_job_update
from server.app.jobs.atomic_mutations import JobMutationConflict
from server.app.scheduler_wakeup import notify_schedulable_work
from server.app.services.job_artifact_staging_scope import staging_output_names
from server.app.services.job_operation_error import JobOperationError, JobOperationResult
from server.app.services.job_rerun.upstream_guard import raise_if_failed_upstream_in_tx
from server.app.services.job_reset_closure import rerun_reset_closure
from server.app.services.job_staged_cleanup import (
    commit_staged_outputs,
    delete_rerun_artifact_objects,
)
from server.app.services.workflow_definitions import require_workspace_active_definition
from server.app.services.workflow_revision_format import definition_from_job_snapshot

if TYPE_CHECKING:
    from server.app.services.job_rerun import JobRerunService
    from server.app.services.job_rerun.recovery_plan import RecoveryPlanGuard

logger = logging.getLogger(__name__)


def commit_rerun(
    service: JobRerunService,
    job: dict[str, Any],
    job_id: str,
    actual_node_key: str,
    *,
    definition: Any | None = None,
    plan_guard: RecoveryPlanGuard | None = None,
) -> JobOperationResult:
    """Commit the existing rerun mutation, optionally constrained by a plan."""
    if definition is None:
        definition = definition_from_job_snapshot(job) or require_workspace_active_definition(
            service.job_db, str(job["workspace_id"]), str(job["workspace_id"])
        )
    seed_nodes = plan_guard.seed_nodes if plan_guard is not None else (actual_node_key,)
    if actual_node_key not in seed_nodes:
        raise ValueError("rerun primary node must be in the reviewed seed set")
    affected = sorted(rerun_reset_closure(definition, seed_nodes))
    stale_nodes = [key for key in affected if key != actual_node_key]
    staged_names = frozenset(staging_output_names(definition, set(affected)))
    downstream_map = (
        {actual_node_key: stale_nodes}
        if plan_guard is None
        else {seed: [key for key in affected if key not in seed_nodes] for seed in seed_nodes}
    )
    staged = None
    deleted_rows: list[dict[str, Any]] = []
    try:
        with service.job_db.lease_guarded_mutation(
            job_id,
            service._now(),
            reject_running_nodes=True,
        ) as conn:
            for seed in seed_nodes:
                raise_if_failed_upstream_in_tx(
                    service.job_db,
                    conn,
                    definition,
                    seed,
                    job_id,
                    "rerun",
                    actual_node_key,
                    stale_nodes=[key for key in affected if key != seed],
                )
            if plan_guard is not None:
                plan_guard.validate(
                    conn,
                    definition=definition,
                    affected_nodes=affected,
                    staged_names=staged_names,
                )
            staged = service.artifact_service.stage_outputs(job, affected, definition)
            deleted_rows = service.job_db.mark_nodes_for_rerun_in_transaction(
                conn,
                job_id,
                list(seed_nodes),
                downstream_map,
                staged_artifact_names=staged.artifact_names,
            )
            if plan_guard is not None:
                plan_guard.validate_retired_rows(deleted_rows)
    except JobMutationConflict as exc:
        if staged is not None:
            staged.rollback()
        raise JobOperationError(
            job_id, "rerun", "skipped", actual_node_key, exc.reason_code, str(exc)
        ) from exc
    except JobOperationError:
        if staged is not None:
            staged.rollback()
        raise
    except ValueError as exc:
        if staged is not None:
            staged.rollback()
        raise JobOperationError(
            job_id, "rerun", "failed", actual_node_key, "cleanup_failed", str(exc)
        ) from exc
    except Exception as exc:
        # #204 broad-except audit: terminal safety net of the staged rerun
        # mutation. The conflict and ValueError arms above preserve their
        # specific outcomes; this arm always restores staged files before
        # normalizing an unexpected failure and keeps its traceback.
        logger.exception("Failed to mark nodes for rerun for job %s", job_id)
        if staged is not None:
            staged.rollback()
        raise JobOperationError(
            job_id, "rerun", "failed", actual_node_key, "rerun_failed", str(exc)
        ) from exc

    commit_staged_outputs(staged, job_id, "rerun")
    delete_rerun_artifact_objects(service.object_store, deleted_rows, job_id, "rerun")
    notify_schedulable_work()
    if service.job_event_buffer is not None:
        record_job_update(service.job_db, service.job_event_buffer, job_id)
    elif service.job_event_manager is not None:
        broadcast_job_update(service.job_db, service.job_event_manager, job_id)
    return {
        "job_id": job_id,
        "operation": "rerun",
        "status": "succeeded",
        "node_key": actual_node_key,
        "reason_code": None,
        "message": None,
    }
