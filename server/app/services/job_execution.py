from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from server.app.events import JobEventManager
from server.app.events.aggregator import broadcast_job_update, record_job_update
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.jobs.atomic_mutations import JobMutationConflict
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_operation_error import JobOperationError, JobOperationResult
from server.app.services.job_rerun.batch_ops import batch_run_to as _batch_run_to
from server.app.services.job_run_to import run_to_with_start, run_to_without_start
from server.app.services.workflow_definitions import require_workspace_active_definition
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.execution_control import ExecutionControlError, ancestor_closure
from server.app.workflows.start_node import START_NODE_TYPE

logger = logging.getLogger(__name__)


class JobExecutionService:
    """Orchestrate run-to-target and continue operations for workspace DAG jobs."""

    def __init__(
        self,
        job_db: JobQueries,
        artifact_mutation: JobArtifactMutationService,
        lease_repo: ExecutorLeaseRepository,
        clock: Callable[[], float] | None = None,
        job_event_manager: JobEventManager | None = None,
        job_event_buffer: Any | None = None,
        object_store: Any = None,
    ) -> None:
        self.job_db = job_db
        self.artifact_mutation = artifact_mutation
        self.lease_repo = lease_repo
        self.clock = clock
        self.job_event_manager = job_event_manager
        self.job_event_buffer = job_event_buffer
        # #508: manifest-row GC needs the post-commit object deletion.
        self.object_store = object_store

    def _now(self) -> datetime:
        if self.clock is not None:
            return datetime.fromtimestamp(self.clock(), tz=UTC)
        return datetime.now(UTC)

    def _result(
        self,
        job_id: str,
        operation: str,
        status: str,
        node_key: str | None = None,
        reason_code: str | None = None,
        message: str | None = None,
    ) -> JobOperationResult:
        return {
            "job_id": job_id,
            "operation": operation,
            "status": status,
            "node_key": node_key,
            "reason_code": reason_code,
            "message": message,
        }

    def _has_active_lease(self, job_id: str) -> bool:
        return self.lease_repo.has_active_for_job(job_id, self._now())

    def _definition(self, job: dict[str, Any]) -> WorkflowDefinition:
        # Jobs without an intake-frozen snapshot fall back to their own
        # workspace's active revision (schema v50), never a global template.
        return definition_from_job_snapshot(job) or require_workspace_active_definition(
            self.job_db, str(job["workspace_id"]), str(job["workspace_id"])
        )

    def run_to(
        self,
        workspace_id: str,
        job_id: str,
        target_node_key: str,
        start_node_key: str | None = None,
    ) -> JobOperationResult:
        job = self.job_db.get_job(job_id)
        if job is None:
            raise JobOperationError(
                job_id, "run_to", "failed", target_node_key, "not_found", "Job not found"
            )
        if job["workspace_id"] != workspace_id:
            raise JobOperationError(
                job_id,
                "run_to",
                "failed",
                target_node_key,
                "not_found",
                "Job not found",
            )
        definition = self._definition(job)
        if target_node_key not in definition.nodes:
            raise JobOperationError(
                job_id,
                "run_to",
                "failed",
                target_node_key,
                "node_not_found",
                f"Node {target_node_key} not found in workflow",
            )
        if definition.nodes[target_node_key].node_type == START_NODE_TYPE:
            raise JobOperationError(
                job_id,
                "run_to",
                "failed",
                target_node_key,
                "node_not_executable",
                f"Node {target_node_key} is an entry (type: start) node and never executes",
            )

        try:
            closure = ancestor_closure(definition, target_node_key)
        except ExecutionControlError as exc:
            raise JobOperationError(
                job_id,
                "run_to",
                "failed",
                target_node_key,
                "node_not_found",
                str(exc),
            ) from exc

        if self._has_active_lease(job_id):
            raise JobOperationError(
                job_id,
                "run_to",
                "skipped",
                target_node_key,
                "busy",
                "Job has an active executor lease",
            )

        if start_node_key is None:
            return run_to_without_start(self, job, definition, target_node_key, closure)
        return run_to_with_start(self, job, definition, target_node_key, start_node_key, closure)

    def continue_job(self, workspace_id: str, job_id: str) -> JobOperationResult:
        job = self.job_db.get_job(job_id)
        if job is None:
            raise JobOperationError(
                job_id, "continue", "failed", None, "not_found", "Job not found"
            )
        if job["workspace_id"] != workspace_id:
            raise JobOperationError(
                job_id,
                "continue",
                "failed",
                None,
                "not_found",
                "Job not found",
            )

        try:
            self.job_db.resume_job(job_id)
        except JobMutationConflict as exc:
            raise JobOperationError(
                job_id,
                "continue",
                "skipped",
                None,
                exc.reason_code,
                str(exc),
            ) from exc
        except ValueError as exc:
            raise JobOperationError(
                job_id,
                "continue",
                "failed",
                None,
                "not_found" if "Job not found" in str(exc) else "resume_failed",
                str(exc),
            ) from exc

        if self.job_event_buffer is not None:
            record_job_update(self.job_db, self.job_event_buffer, job_id, str(job["workspace_id"]))
        elif self.job_event_manager is not None:
            broadcast_job_update(self.job_db, self.job_event_manager, job_id)
        return self._result(job_id, "continue", "succeeded")

    def batch_run_to(
        self,
        workspace_id: str,
        job_ids: list[str] | None,
        target_node_key: str,
        start_node_key: str | None = None,
        **kwargs: Any,
    ) -> list[JobOperationResult]:
        """Run the selected jobs to a target node; kwargs take job_filter/exclude_ids."""
        return _batch_run_to(self, workspace_id, job_ids, target_node_key, start_node_key, **kwargs)
