from collections.abc import Callable, Collection
from datetime import UTC, datetime
from typing import Any

from server.app.events import JobEventManager
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services._job_rerun_by_failure import rerun_by_failure_category as _rerun_by_failure
from server.app.services._job_rerun_single import execute_rerun, resolve_rerun_node
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_operation_error import JobOperationError, JobOperationResult
from server.app.services.job_selection_resolver import resolve_batch_selection
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.settings import Settings


class JobRerunService:
    def __init__(
        self,
        job_db: JobQueries,
        lease_repo: ExecutorLeaseRepository,
        settings: Settings,
        workflows: WorkflowCatalogService,
        artifact_service: JobArtifactMutationService | None = None,
        clock: Callable[[], float] | None = None,
        job_event_manager: JobEventManager | None = None,
        job_event_buffer: Any | None = None,
    ) -> None:
        self.job_db = job_db
        self.lease_repo = lease_repo
        self.settings = settings
        self.workflows = workflows
        self.artifact_service = artifact_service or JobArtifactMutationService(settings.jobs_dir)
        self.clock = clock
        self.job_event_manager = job_event_manager
        self.job_event_buffer = job_event_buffer

    def _now(self) -> datetime:
        if self.clock is not None:
            return datetime.fromtimestamp(self.clock(), tz=UTC)
        return datetime.now(UTC)

    def _job_has_running_nodes(self, job_id: str) -> bool:
        return any(node["status"] == "running" for node in self.job_db.list_job_nodes(job_id))

    def rerun(
        self,
        workspace_id: str,
        job_id: str,
        node_key: str | None = None,
        *,
        from_failed_node: bool = False,
    ) -> JobOperationResult:
        job = self.job_db.get_job(job_id)
        if job is None:
            raise JobOperationError(
                job_id, "rerun", "failed", node_key, "not_found", "Job not found"
            )
        if job["workspace_id"] != workspace_id:
            raise JobOperationError(
                job_id,
                "rerun",
                "failed",
                node_key,
                "wrong_workspace",
                f"Job does not belong to workspace {workspace_id}",
            )

        actual_node_key = resolve_rerun_node(self.job_db, job_id, job, node_key, from_failed_node)
        return execute_rerun(self, job, job_id, actual_node_key)

    def rerun_by_failure_category(
        self, workspace_id: str, category: str, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return _rerun_by_failure(self, workspace_id, category, **kwargs)

    def batch_rerun(
        self,
        workspace_id: str,
        job_ids: list[str] | None = None,
        node_key: str | None = None,
        *,
        from_failed_node: bool = False,
        job_filter: JobListFilter | None = None,
        exclude_ids: Collection[str] = (),
    ) -> list[JobOperationResult]:
        ids = resolve_batch_selection(self.job_db, workspace_id, job_ids, job_filter, exclude_ids)
        results: list[JobOperationResult] = []
        for job_id in self._normalize_values(ids):
            try:
                results.append(
                    self.rerun(workspace_id, job_id, node_key, from_failed_node=from_failed_node)
                )
            except JobOperationError as exc:
                results.append(exc.to_result())
        return results

    @staticmethod
    def _normalize_values(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))
