from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.pipelines.definition import PipelineDefinition
from server.app.pipelines.execution_control import ExecutionControlError, ancestor_closure
from server.app.pipelines.scheduler import downstream_nodes
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.pipeline_catalog import PipelineCatalogService

logger = logging.getLogger(__name__)


class JobExecutionService:
    """Orchestrate run-to-target and continue operations for workspace DAG jobs."""

    def __init__(
        self,
        job_db: JobQueries,
        artifact_mutation: JobArtifactMutationService,
        lease_repo: ExecutorLeaseRepository,
        pipelines: PipelineCatalogService,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.job_db = job_db
        self.artifact_mutation = artifact_mutation
        self.lease_repo = lease_repo
        self.pipelines = pipelines
        self.clock = clock

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

    def _get_job(self, workspace_id: str, job_id: str) -> dict[str, Any] | None:
        job = self.job_db.get_job(job_id)
        if job is None:
            return None
        if job["workspace_id"] != workspace_id:
            return None
        return job

    def _has_active_lease(self, job_id: str) -> bool:
        return self.lease_repo.has_active_for_job(job_id, self._now())

    def _definition(self, pipeline_key: str) -> PipelineDefinition:
        return self.pipelines.definition(pipeline_key)

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

        definition = self._definition(str(job["pipeline_key"]))
        if target_node_key not in definition.nodes:
            return self._result(
                job_id,
                "run_to",
                "failed",
                target_node_key,
                "node_not_found",
                f"Node {target_node_key} not found in pipeline",
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
        definition: PipelineDefinition,
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
            self.job_db.set_job_execution_target(job_id, target_node_key)
        except ValueError as exc:
            return self._result(
                job_id,
                "run_to",
                "failed",
                target_node_key,
                "node_not_found",
                str(exc),
            )

        with self.job_db.connect() as conn:
            for node_key in closure:
                if node_statuses.get(node_key) == "completed":
                    continue
                conn.execute(
                    """
                    update job_nodes
                    set status='pending',
                        stale_reason='',
                        error_message='',
                        started_at=null,
                        finished_at=null
                    where job_id=? and node_key=?
                    """,
                    (job_id, node_key),
                )
            conn.execute(
                """
                update jobs
                set status='queued',
                    execution_paused=0,
                    pause_reason='',
                    error_message='',
                    updated_at=current_timestamp
                where id=? and status in ('paused', 'failed', 'completed')
                """,
                (job_id,),
            )

        return self._result(job_id, "run_to", "succeeded", target_node_key)

    def _run_to_with_start(
        self,
        job: dict[str, Any],
        definition: PipelineDefinition,
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
                f"Start node {start_node_key} not found in pipeline",
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

        try:
            staged = self.artifact_mutation.stage_outputs(
                job, [start_node_key], definition, closure=closure
            )
        except ValueError as exc:
            return self._result(
                job_id,
                "run_to",
                "failed",
                target_node_key,
                "cleanup_failed",
                str(exc),
            )

        try:
            descendants = downstream_nodes(definition, start_node_key)
            self.job_db.mark_nodes_for_rerun_atomic(
                job_id, [start_node_key], {start_node_key: descendants}
            )
            self.job_db.set_job_execution_target(job_id, target_node_key)
            with self.job_db.connect() as conn:
                conn.execute(
                    """
                    update jobs
                    set execution_paused=0,
                        pause_reason='',
                        updated_at=current_timestamp
                    where id=?
                    """,
                    (job_id,),
                )
        except Exception as exc:
            logger.exception("Failed to persist run-to target for job %s", job_id)
            staged.rollback()
            return self._result(
                job_id,
                "run_to",
                "failed",
                target_node_key,
                "rerun_failed",
                str(exc),
            )

        staged.commit()
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
        except ValueError as exc:
            return self._result(
                job_id,
                "continue",
                "failed",
                None,
                "not_found" if "Job not found" in str(exc) else "resume_failed",
                str(exc),
            )

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
