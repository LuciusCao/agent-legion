from __future__ import annotations

import glob
import logging
import os
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.jobs.atomic_mutations import JobMutationConflict
from server.app.settings import Settings
from server.app.storage_paths import resolve_managed_path

logger = logging.getLogger(__name__)


class DeletionRollbackConflict(RuntimeError):
    """Raised when a deletion rollback cannot safely restore the staged path.

    The staged recovery path is preserved so an operator can reconcile the
    conflict manually.
    """

    def __init__(self, staged_path: Path, original_path: Path) -> None:
        super().__init__(
            f"Cannot restore {staged_path}: destination {original_path} already exists"
        )
        self.staged_path = staged_path
        self.original_path = original_path


class JobDeleteResult(TypedDict):
    job_id: str
    operation: str
    status: str
    reason_code: str | None
    message: str | None


class JobDeletionService:
    def __init__(
        self,
        job_db: JobQueries,
        lease_repo: ExecutorLeaseRepository,
        settings: Settings,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.job_db = job_db
        self.lease_repo = lease_repo
        self.settings = settings
        self.clock = clock

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
            return self._result(job_id, "failed", "not_found", "Job not found")
        if job["workspace_id"] != workspace_id:
            return self._result(
                job_id,
                "failed",
                "wrong_workspace",
                f"Job does not belong to workspace {workspace_id}",
            )
        if self.lease_repo.has_active_for_job(job_id, self._now()):
            return self._result(
                job_id,
                "failed",
                "active_lease",
                "Cannot delete a job with an active executor lease",
            )

        log_paths = [
            Path(log_path)
            for log_path in glob.glob(str(self.settings.logs_dir / "jobs" / f"{job_id}-*.log"))
        ]

        operation_id = f"{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}-{uuid.uuid4().hex[:8]}"
        staged_storage: Path | None = None
        staged_logs: list[Path] = []
        restore_paths: list[tuple[Path, Path]] = []

        try:
            storage_dir = resolve_managed_path(
                self.settings.jobs_dir,
                job["storage_dir"],
                allow_missing=True,
                record_id=job_id,
                root_kind="job",
            )
            with self.job_db.lease_guarded_mutation(
                job_id,
                self._now(),
                reject_running_nodes=True,
            ) as conn:
                if storage_dir.exists() and storage_dir.is_dir():
                    trash_dir = self.settings.jobs_dir / ".trash" / operation_id
                    trash_dir.mkdir(parents=True, exist_ok=True)
                    staged_storage = trash_dir / storage_dir.name
                    shutil.move(str(storage_dir), str(staged_storage))
                    restore_paths.append((staged_storage, storage_dir))

                if log_paths:
                    log_trash_dir = self.settings.logs_dir / "jobs" / ".trash" / operation_id
                    log_trash_dir.mkdir(parents=True, exist_ok=True)
                    for log_path in log_paths:
                        staged_log = log_trash_dir / log_path.name
                        shutil.move(str(log_path), str(staged_log))
                        staged_logs.append(staged_log)
                        restore_paths.append((staged_log, log_path))

                self.job_db.delete_job_in_transaction(conn, job_id)
        except JobMutationConflict as exc:
            try:
                self._restore_paths(restore_paths)
            except DeletionRollbackConflict as rollback_exc:
                return self._result(
                    job_id,
                    "failed",
                    "rollback_conflict",
                    str(rollback_exc),
                )
            reason_code = "active_lease" if "lease" in str(exc).lower() else "delete_failed"
            return self._result(job_id, "failed", reason_code, str(exc))
        except Exception as exc:
            logger.exception("Unexpected error deleting job %s", job_id)
            try:
                self._restore_paths(restore_paths)
            except DeletionRollbackConflict as rollback_exc:
                return self._result(
                    job_id,
                    "failed",
                    "rollback_conflict",
                    str(rollback_exc),
                )
            return self._result(job_id, "failed", "delete_failed", str(exc))

        self._cleanup_staged_paths(job_id, staged_storage, staged_logs)
        self._prune_empty_trash(self.settings.jobs_dir / ".trash" / operation_id)
        self._prune_empty_trash(self.settings.logs_dir / "jobs" / ".trash" / operation_id)
        return self._result(job_id, "succeeded")

    def batch_delete(self, workspace_id: str, job_ids: list[str]) -> list[JobDeleteResult]:
        results: list[JobDeleteResult] = []
        seen: set[str] = set()
        for job_id in job_ids:
            normalized = job_id.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            results.append(self.delete(workspace_id, normalized))
        return results

    @staticmethod
    def _prune_empty_trash(path: Path) -> None:
        try:
            if path.exists() and not any(path.iterdir()):
                path.rmdir()
                parent = path.parent
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
        except OSError:
            pass

    @staticmethod
    def _restore_paths(restore_paths: list[tuple[Path, Path]]) -> None:
        """Restore staged paths atomically when the destination is absent.

        If the destination already exists (e.g., a concurrent recreation), raise
        ``DeletionRollbackConflict`` and leave both the destination and the staged
        recovery path untouched.
        """
        for staged, original in reversed(restore_paths):
            if not staged.exists():
                continue
            original.parent.mkdir(parents=True, exist_ok=True)
            if original.exists():
                raise DeletionRollbackConflict(staged, original)
            os.replace(str(staged), str(original))

    @staticmethod
    def _cleanup_staged_paths(
        job_id: str,
        staged_storage: Path | None,
        staged_logs: list[Path],
    ) -> None:
        try:
            if staged_storage is not None and staged_storage.exists():
                shutil.rmtree(staged_storage)
            for staged_log in staged_logs:
                if staged_log.exists():
                    staged_log.unlink(missing_ok=True)
        except Exception:
            logger.exception("Failed to clean staged files after deleting job %s", job_id)
