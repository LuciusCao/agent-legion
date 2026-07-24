from __future__ import annotations

from server.app.jobs import JobQueries
from server.app.services.workspace_package_contracts import JobPackageItemResult


class WorkspacePackageClearPackedMixin:
    job_db: JobQueries

    def _result(
        self, job_id: str, status: str, reason_code: str | None = None, message: str | None = None
    ) -> JobPackageItemResult:
        return {"job_id": job_id, "status": status, "reason_code": reason_code, "message": message}

    def clear_packed_status(
        self, workspace_id: str, job_ids: list[str]
    ) -> list[JobPackageItemResult]:
        results: list[JobPackageItemResult] = []
        eligible_job_ids: list[str] = []
        seen: set[str] = set()

        for job_id in job_ids:
            normalized = job_id.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)

            job = self.job_db.get_job(normalized)
            if job is None:
                results.append(self._result(normalized, "failed", "not_found", "Job not found"))
                continue
            if job["workspace_id"] != workspace_id:
                results.append(
                    self._result(
                        normalized,
                        "failed",
                        "wrong_workspace",
                        f"Job does not belong to workspace {workspace_id}",
                    )
                )
                continue
            eligible_job_ids.append(normalized)
            results.append(self._result(normalized, "succeeded"))

        self.job_db.set_jobs_packed(eligible_job_ids, packed=0)
        return results
