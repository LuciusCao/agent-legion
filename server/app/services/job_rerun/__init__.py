"""Job rerun service and its set-based batch / preview / eligibility modules.

Issue #199 归包：包根即原 ``services/job_rerun.py``（``JobRerunService``
门面），``single.py`` 单节点路径、``batch.py`` 批量路径、``preview.py`` /
``preview_checks.py`` 只读预览、``eligibility.py`` 共享资格规则、
``upstream_guard.py`` 失败上游守卫、``by_failure_results.py`` 按失败
类别批量的逐 job 结果组装、``batch_ops.py`` 批量 remove / run-to 循环。
"""

from collections.abc import Callable, Collection
from datetime import UTC, datetime
from typing import Any

from server.app.events import JobEventManager
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.job_artifact_mutation import JobArtifactMutationService
from server.app.services.job_operation_error import JobOperationError, JobOperationResult
from server.app.services.job_rerun.batch import batch_rerun as _batch_rerun
from server.app.services.job_rerun.batch import (
    rerun_by_failure_category as _rerun_by_failure,
)
from server.app.services.job_rerun.single import execute_rerun, resolve_rerun_node
from server.app.settings import Settings


class JobRerunService:
    def __init__(
        self,
        job_db: JobQueries,
        lease_repo: ExecutorLeaseRepository,
        settings: Settings,
        artifact_service: JobArtifactMutationService | None = None,
        clock: Callable[[], float] | None = None,
        job_event_manager: JobEventManager | None = None,
        job_event_buffer: Any | None = None,
    ) -> None:
        self.job_db = job_db
        self.lease_repo = lease_repo
        self.settings = settings
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
        """Set-based batch rerun: one prefetch, in-memory planning, writes only
        for eligible jobs (per-job results identical to looping ``rerun()``)."""
        return _batch_rerun(
            self,
            workspace_id,
            job_ids,
            node_key,
            from_failed_node=from_failed_node,
            job_filter=job_filter,
            exclude_ids=exclude_ids,
        )

    @staticmethod
    def _normalize_values(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))
