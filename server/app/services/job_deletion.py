from __future__ import annotations

import glob
import logging
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.settings import Settings

logger = logging.getLogger(__name__)


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

        storage_dir = Path(str(job["storage_dir"]))
        log_paths = [
            Path(log_path)
            for log_path in glob.glob(str(self.settings.logs_dir / "jobs" / f"{job_id}-*.log"))
        ]

        operation_id = f"{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}-{uuid.uuid4().hex[:8]}"
        staged_storage: Path | None = None
        staged_logs: list[Path] = []
        restore_paths: list[tuple[Path, Path]] = []

        try:
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

            try:
                self.job_db.delete_job(job_id)
            except ValueError as exc:
                # Database operation failed; restore staged paths before re-raising.
                for staged, original in restore_paths:
                    if staged.exists():
                        staged.parent.mkdir(parents=True, exist_ok=True)
                        original.parent.mkdir(parents=True, exist_ok=True)
                        if original.exists():
                            if original.is_dir():
                                shutil.rmtree(original)
                            else:
                                original.unlink()
                        shutil.move(str(staged), str(original))
                return self._result(job_id, "failed", "delete_failed", str(exc))

            # Database rows removed; clean up staged files.
            if staged_storage is not None and staged_storage.exists():
                shutil.rmtree(staged_storage)
            for staged_log in staged_logs:
                if staged_log.exists():
                    staged_log.unlink(missing_ok=True)

            # Clean up empty trash directories (best effort).
            self._prune_empty_trash(self.settings.jobs_dir / ".trash" / operation_id)
            self._prune_empty_trash(self.settings.logs_dir / "jobs" / ".trash" / operation_id)

            return self._result(job_id, "succeeded")
        except Exception as exc:
            logger.exception("Unexpected error deleting job %s", job_id)
            return self._result(job_id, "failed", "delete_failed", str(exc))

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
