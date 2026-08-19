"""Operator-driven job pause/resume (execution_paused flag).

Pausing only blocks new dispatch/claim: in-flight nodes keep running, and a
job whose nodes all settle is projected to ``status='paused'`` by the lease
layer. Terminal jobs are never paused, and run-to pauses
(``pause_reason='target_reached'``) stay owned by the continue flow.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.events import JobEventManager
from server.app.events.aggregator import broadcast_job_update, record_job_update
from server.app.jobs import JobQueries
from server.app.jobs.queries.job_filtering import JobListFilter
from server.app.services.job_operation_error import JobOperationError, JobOperationResult
from server.app.services.job_selection_resolver import resolve_batch_selection

if TYPE_CHECKING:
    from collections.abc import Collection

_TERMINAL_JOB_STATUSES = ("completed", "failed")


class JobPauseService:
    def __init__(
        self,
        job_db: JobQueries,
        job_event_manager: JobEventManager | None = None,
        job_event_buffer: Any | None = None,
    ) -> None:
        self.job_db = job_db
        self.job_event_manager = job_event_manager
        self.job_event_buffer = job_event_buffer

    def _result(
        self,
        job_id: str,
        operation: str,
        status: str,
        reason_code: str | None = None,
        message: str | None = None,
    ) -> JobOperationResult:
        return {
            "job_id": job_id,
            "operation": operation,
            "status": status,
            "node_key": None,
            "reason_code": reason_code,
            "message": message,
        }

    def _job_or_raise(self, workspace_id: str, job_id: str, operation: str) -> dict[str, Any]:
        job = self.job_db.get_job(job_id)
        if job is None:
            raise JobOperationError(job_id, operation, "failed", None, "not_found", "Job not found")
        if job["workspace_id"] != workspace_id:
            raise JobOperationError(
                job_id,
                operation,
                "failed",
                None,
                "wrong_workspace",
                f"Job does not belong to workspace {workspace_id}",
            )
        return job

    def _record_update(self, job_id: str, workspace_id: str) -> None:
        if self.job_event_buffer is not None:
            record_job_update(self.job_db, self.job_event_buffer, job_id, workspace_id)
        elif self.job_event_manager is not None:
            broadcast_job_update(self.job_db, self.job_event_manager, job_id)

    def pause(
        self,
        workspace_id: str,
        job_id: str,
        reason: str | None = None,
        *,
        operator: str = "",
    ) -> JobOperationResult:
        job = self._job_or_raise(workspace_id, job_id, "pause")
        if job["status"] in _TERMINAL_JOB_STATUSES:
            return self._result(job_id, "pause", "skipped", "terminal", f"Job is {job['status']}")
        if job["execution_paused"]:
            return self._result(job_id, "pause", "skipped", "already_paused")
        clean_reason = (reason or "").strip()
        stored_reason = (
            f"{clean_reason} ({operator})"
            if clean_reason and operator
            else clean_reason or operator
        )
        if not self.job_db.mark_execution_paused(job_id, stored_reason):
            return self._result(
                job_id, "pause", "skipped", "state_changed", "Job state changed during pause"
            )
        self._record_update(job_id, workspace_id)
        return self._result(job_id, "pause", "succeeded")

    def resume(self, workspace_id: str, job_id: str) -> JobOperationResult:
        job = self._job_or_raise(workspace_id, job_id, "resume")
        if not job["execution_paused"]:
            return self._result(job_id, "resume", "skipped", "not_paused")
        if job["pause_reason"] == "target_reached":
            return self._result(
                job_id,
                "resume",
                "skipped",
                "target_reached",
                "Job is paused by run-to; use continue instead",
            )
        if not self.job_db.clear_execution_paused(job_id):
            return self._result(
                job_id, "resume", "skipped", "state_changed", "Job state changed during resume"
            )
        self._record_update(job_id, workspace_id)
        return self._result(job_id, "resume", "succeeded")

    def batch_pause(
        self,
        workspace_id: str,
        job_ids: list[str] | None = None,
        reason: str | None = None,
        *,
        operator: str = "",
        job_filter: JobListFilter | None = None,
        exclude_ids: Collection[str] = (),
    ) -> list[JobOperationResult]:
        """Pause each selected job; explicit ids and filters resolve the same way."""
        ids = resolve_batch_selection(self.job_db, workspace_id, job_ids, job_filter, exclude_ids)
        results: list[JobOperationResult] = []
        for job_id in ids:
            try:
                results.append(self.pause(workspace_id, job_id, reason, operator=operator))
            except JobOperationError as exc:
                results.append(exc.to_result())
        return results

    def batch_resume(
        self,
        workspace_id: str,
        job_ids: list[str] | None = None,
        *,
        job_filter: JobListFilter | None = None,
        exclude_ids: Collection[str] = (),
    ) -> list[JobOperationResult]:
        """Resume each selected job; explicit ids and filters resolve the same way."""
        ids = resolve_batch_selection(self.job_db, workspace_id, job_ids, job_filter, exclude_ids)
        results: list[JobOperationResult] = []
        for job_id in ids:
            try:
                results.append(self.resume(workspace_id, job_id))
            except JobOperationError as exc:
                results.append(exc.to_result())
        return results
