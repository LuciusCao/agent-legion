from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, NoReturn, TypedDict

from server.app.events import JobEventManager
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.jobs.atomic_mutations import JobMutationConflict
from server.app.services.artifact_store import ArtifactStore
from server.app.services.job_artifact_gc import gc_deleted_job_artifacts, read_artifact_candidates
from server.app.services.job_deletion_trash import purge_deleted_job_files
from server.app.services.job_operation_error import JobOperationError
from server.app.services.job_rerun.batch_ops import batch_delete as _batch_delete
from server.app.settings import Settings
from server.app.storage_paths import ManagedPathError, resolve_job_dir

logger = logging.getLogger(__name__)


class JobDeleteResult(TypedDict):
    job_id: str
    operation: str
    status: str
    reason_code: str | None
    message: str | None


def _fail(job_id: str, reason_code: str | None, message: str) -> NoReturn:
    raise JobOperationError(job_id, "delete", "failed", None, reason_code, message)


class JobDeletionService:
    def __init__(
        self,
        job_db: JobQueries,
        lease_repo: ExecutorLeaseRepository,
        settings: Settings,
        clock: Callable[[], float] | None = None,
        job_event_manager: JobEventManager | None = None,
        job_event_buffer: Any | None = None,
        artifact_store: ArtifactStore | None = None,
        object_store: Any = None,
    ) -> None:
        self.job_db = job_db
        self.lease_repo = lease_repo
        self.settings = settings
        self.clock = clock
        self.job_event_manager = job_event_manager
        self.job_event_buffer = job_event_buffer
        self.artifact_store = artifact_store
        self.object_store = object_store

    def _now(self) -> datetime:
        if self.clock is not None:
            return datetime.fromtimestamp(self.clock(), tz=UTC)
        return datetime.now(UTC)

    def _result(
        self,
        job_id: str,
        status: str,
        reason_code: str | None = None,
        message: str | None = None,
    ) -> JobDeleteResult:
        return {
            "job_id": job_id,
            "operation": "delete",
            "status": status,
            "reason_code": reason_code,
            "message": message,
        }

    def delete(self, workspace_id: str, job_id: str) -> JobDeleteResult:
        job = self.job_db.get_job(job_id)
        if job is None:
            _fail(job_id, "not_found", "Job not found")
        if job["workspace_id"] != workspace_id:
            _fail(job_id, "not_found", "Job not found")
        if self.lease_repo.has_active_for_job(job_id, self._now()):
            _fail(job_id, "active_lease", "Cannot delete a job with an active executor lease")

        try:
            # 事务前 fail-closed：路径逃逸就拒绝删除，行与文件都不动。
            resolve_job_dir(job, self.settings.jobs_dir)
        except ManagedPathError as exc:
            _fail(job_id, "delete_failed", str(exc))

        artifact_candidates = read_artifact_candidates(self.artifact_store, job_id)
        # D12: snapshot the object-storage manifest rows before the job row
        # cascades them away; the objects are reclaimed after commit.
        object_rows = (
            self.object_store.rows_for_job(job_id)
            if self.object_store is not None and self.object_store.enabled
            else []
        )
        operation_id = f"{self._now().strftime('%Y%m%d%H%M%S%f')}-{uuid.uuid4().hex[:8]}"

        # #958：事务只做 DB 删除，不碰文件系统（文件 I/O 不再拉长 job-mutation
        # 锁的持有时间）；本地 job_dir / 日志在提交后由 purge_deleted_job_files
        # 清理，失败点终态与「先提交后移动」的取舍见 job_deletion_trash 模块。
        try:
            with self.job_db.lease_guarded_mutation(
                job_id,
                self._now(),
                reject_running_nodes=True,
            ) as conn:
                self.job_db.delete_job_in_transaction(conn, job_id)
        except JobMutationConflict as exc:
            _fail(job_id, exc.reason_code, str(exc))
        except Exception as exc:
            # #204 broad-except audit: the transaction now carries only the DB
            # write (delete_job_in_transaction, whose ValueError carries the
            # business refusals — a foreign-key rejection from a still-
            # referenced job, or a concurrent delete that already removed the
            # row — and is deliberately NOT caught before this arm so it is
            # normalized here). The filesystem has not been touched yet, so
            # every failure leaves the row and the local files intact and is
            # normalized to JobOperationError; the conflict arm above already
            # peeled off the concurrency case. logger.exception keeps the
            # traceback of the unexpected kind.
            logger.exception("Unexpected error deleting job %s", job_id)
            _fail(job_id, "delete_failed", str(exc))

        purge_deleted_job_files(job, self.settings, operation_id)
        gc_deleted_job_artifacts(self.artifact_store, job_id, artifact_candidates)
        if object_rows and self.object_store is not None:
            self.object_store.delete_objects(object_rows)
        if self.job_event_buffer is not None:
            self.job_event_buffer.record_job_deleted(workspace_id, job_id)
        elif self.job_event_manager is not None:
            stats = self.job_db.count_jobs_by_status(workspace_id)
            self.job_event_manager.broadcast_job_deleted(workspace_id, job_id, stats)
        return self._result(job_id, "succeeded")

    def batch_delete(
        self, workspace_id: str, job_ids: list[str] | None = None, **kwargs: Any
    ) -> list[JobDeleteResult]:
        """Delete the selected jobs; kwargs take job_filter/exclude_ids."""
        return _batch_delete(self, workspace_id, job_ids, **kwargs)
