from __future__ import annotations

import logging
from typing import Any, TypedDict

from server.app.jobs import JobQueries
from server.app.pipeline.package import create_workspace_package
from server.app.settings import Settings
from server.app.storage_paths import make_data_relative

logger = logging.getLogger(__name__)


class JobPackageItemResult(TypedDict):
    job_id: str
    status: str
    reason_code: str | None
    message: str | None


class JobPackageResult(TypedDict):
    results: list[JobPackageItemResult]
    succeeded_count: int
    failed_count: int
    package_filename: str | None
    download_url: str | None


class JobPackageService:
    def __init__(self, job_db: JobQueries, settings: Settings) -> None:
        self.job_db = job_db
        self.settings = settings

    def _result(
        self, job_id: str, status: str, reason_code: str | None = None, message: str | None = None
    ) -> JobPackageItemResult:
        return {"job_id": job_id, "status": status, "reason_code": reason_code, "message": message}

    def package(self, workspace_id: str, job_ids: list[str]) -> JobPackageResult:
        results: list[JobPackageItemResult] = []
        eligible_jobs: list[dict[str, Any]] = []
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
            if job.get("status") != "completed":
                results.append(
                    self._result(
                        normalized,
                        "failed",
                        "not_completed",
                        "Job is not completed",
                    )
                )
                continue
            eligible_jobs.append(job)
            results.append(self._result(normalized, "succeeded"))

        succeeded_count = sum(1 for r in results if r["status"] == "succeeded")
        failed_count = len(results) - succeeded_count

        response: JobPackageResult = {
            "results": results,
            "succeeded_count": succeeded_count,
            "failed_count": failed_count,
            "package_filename": None,
            "download_url": None,
        }

        if not eligible_jobs:
            return response

        workspace_packages_dir = self.settings.packages_dir / f"workspace-{workspace_id}"
        workspace_packages_dir.mkdir(parents=True, exist_ok=True)
        package_path, count = create_workspace_package(
            eligible_jobs, workspace_packages_dir, self.settings.jobs_dir
        )
        package_filename = package_path.name
        download_url = f"/api/workspaces/{workspace_id}/packages/{package_filename}"

        size_bytes = package_path.stat().st_size
        relative_path = make_data_relative(package_path, self.settings.data_dir)
        name = f"批次 ({count}个任务)"
        self.job_db.insert_workspace_package(
            workspace_id, relative_path, name=name, job_count=count, size_bytes=size_bytes
        )
        self.job_db.set_jobs_packed([job["id"] for job in eligible_jobs], packed=1)

        response["package_filename"] = package_filename
        response["download_url"] = download_url
        return response
