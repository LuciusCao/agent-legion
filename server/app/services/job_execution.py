from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from server.app.events import JobEventManager, broadcast_job_update
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.jobs.atomic_mutations import JobMutationConflict
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_staged_cleanup import commit_staged_outputs
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_revisions import definition_from_job_snapshot
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.execution_control import ExecutionControlError, ancestor_closure
from server.app.workflows.scheduler import downstream_nodes

logger = logging.getLogger(__name__)


class JobExecutionService:
    """Orchestrate run-to-target and continue operations for workspace DAG jobs."""

    def __init__(
        self,
        job_db: JobQueries,
        artifact_mutation: JobArtifactMutationService,
        lease_repo: ExecutorLeaseRepository,
        workflows: WorkflowCatalogService,
        clock: Callable[[], float] | None = None,
        job_event_manager: JobEventManager | None = None,
    ) -> None:
        self.job_db = job_db
        self.artifact_mutation = artifact_mutation
        self.lease_repo = lease_repo
        self.workflows = workflows
        self.clock = clock
        self.job_event_manager = job_event_manager

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
    ) -> dict[str, Any]:
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
        return definition_from_job_snapshot(job) or self.workflows.definition(
            str(job["workflow_key"])
        )

    def run_to(
        self,
        workspace_id: str,
        job_id: str,
        target_node_key: str,
        start_node_key: str | None = None,
    ) -> dict[str, Any]:
        job = self.job_db.get_job(job_id)
        if job is None:
            return self._result(
                job_id, "run_to", "failed", target_node_key, "not_found", "Job not found"
            )
        if job["workspace_id"] != workspace_id:
            return self._result(
                job_id,
                "run_to",
                "failed",
                target_node_key,
                "wrong_workspace",
                f"Job does not belong to workspace {workspace_id}",
            )

        definition = self._definition(job)
        if target_node_key not in definition.nodes:
            return self._result(
                job_id,
                "run_to",
                "failed",
                target_node_key,
                "node_not_found",
                f"Node {target_node_key} not found in workflow",
            )

        try:
            closure = ancestor_closure(definition, target_node_key)
        except ExecutionControlError as exc:
            return self._result(
                job_id,
                "run_to",
                "failed",
                target_node_key,
                "node_not_found",
                str(exc),
            )

        if self._has_active_lease(job_id):
            return self._result(
                job_id,
                "run_to",
                "skipped",
                target_node_key,
                "busy",
                "Job has an active executor lease",
            )

        if start_node_key is None:
            return self._run_to_without_start(job, definition, target_node_key, closure)
        return self._run_to_with_start(job, definition, target_node_key, start_node_key, closure)

    def _run_to_without_start(
        self,
        job: dict[str, Any],
        definition: WorkflowDefinition,
        target_node_key: str,
        closure: frozenset[str],
    ) -> dict[str, Any]:
        job_id = str(job["id"])
        node_statuses = {
            node["node_key"]: node["status"] for node in self.job_db.list_job_nodes(job_id)
        }

        if node_statuses.get(target_node_key) == "completed":
            return self._result(
                job_id,
                "run_to",
                "skipped",
                target_node_key,
                "target_already_completed",
                "Target node is already completed",
            )

        try:
            self.job_db.apply_run_to_atomic(
                job_id,
                target_node_key,
                closure,
                now=self._now(),
            )
        except JobMutationConflict as exc:
            return self._result(
                job_id,
                "run_to",
                "skipped",
                target_node_key,
                exc.reason_code,
                str(exc),
            )
        except ValueError as exc:
            return self._result(
                job_id, "run_to", "failed", target_node_key, "node_not_found", str(exc)
            )

        broadcast_job_update(self.job_db, self.job_event_manager, job_id)
        return self._result(job_id, "run_to", "succeeded", target_node_key)

    def _run_to_with_start(
        self,
        job: dict[str, Any],
        definition: WorkflowDefinition,
        target_node_key: str,
        start_node_key: str,
        closure: frozenset[str],
    ) -> dict[str, Any]:
        job_id = str(job["id"])
        if start_node_key not in definition.nodes:
            return self._result(
                job_id,
                "run_to",
                "failed",
                target_node_key,
                "node_not_found",
                f"Start node {start_node_key} not found in workflow",
            )

        if start_node_key not in closure:
            return self._result(
                job_id,
                "run_to",
                "failed",
                target_node_key,
                "invalid_start",
                f"Start node {start_node_key} is not in the target closure",
            )

        staged = None
        try:
            descendants = downstream_nodes(definition, start_node_key)
            with self.job_db.lease_guarded_mutation(
                job_id,
                self._now(),
                reject_running_nodes=True,
            ) as conn:
                staged = self.artifact_mutation.stage_outputs(
                    job, [start_node_key], definition, closure=closure
                )
                self.job_db.mark_nodes_for_rerun_in_transaction(
                    conn, job_id, [start_node_key], {start_node_key: descendants}
                )
                self.job_db.set_run_to_control_in_transaction(conn, job_id, target_node_key)
        except JobMutationConflict as exc:
            if staged is not None:
                staged.rollback()
            return self._result(
                job_id, "run_to", "skipped", target_node_key, exc.reason_code, str(exc)
            )
        except ValueError as exc:
            if staged is not None:
                staged.rollback()
            return self._result(
                job_id, "run_to", "failed", target_node_key, "cleanup_failed", str(exc)
            )
        except Exception as exc:
            logger.exception("Failed to persist run-to target for job %s", job_id)
            if staged is not None:
                staged.rollback()
            return self._result(
                job_id,
                "run_to",
                "failed",
                target_node_key,
                "rerun_failed",
                str(exc),
            )

        commit_staged_outputs(staged, job_id, "run-to")
        broadcast_job_update(self.job_db, self.job_event_manager, job_id)
        return self._result(job_id, "run_to", "succeeded", target_node_key)

    def continue_job(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        job = self.job_db.get_job(job_id)
        if job is None:
            return self._result(job_id, "continue", "failed", None, "not_found", "Job not found")
        if job["workspace_id"] != workspace_id:
            return self._result(
                job_id,
                "continue",
                "failed",
                None,
                "wrong_workspace",
                f"Job does not belong to workspace {workspace_id}",
            )

        try:
            self.job_db.resume_job(job_id)
        except JobMutationConflict as exc:
            return self._result(
                job_id,
                "continue",
                "skipped",
                None,
                exc.reason_code,
                str(exc),
            )
        except ValueError as exc:
            return self._result(
                job_id,
                "continue",
                "failed",
                None,
                "not_found" if "Job not found" in str(exc) else "resume_failed",
                str(exc),
            )

        broadcast_job_update(self.job_db, self.job_event_manager, job_id)
        return self._result(job_id, "continue", "succeeded")

    def batch_run_to(
        self,
        workspace_id: str,
        job_ids: list[str],
        target_node_key: str,
        start_node_key: str | None = None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for job_id in job_ids:
            results.append(self.run_to(workspace_id, job_id, target_node_key, start_node_key))
        return results
