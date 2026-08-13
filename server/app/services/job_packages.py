from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.pipeline.workspace_package import (
    WORKSPACE_PACKAGE_FILES,
    create_workspace_package,
)
from server.app.services.job_selection_resolver import (
    EmptyJobSelectionError,
    resolve_batch_selection,
)
from server.app.services.workspace_package_artifacts import workspace_artifact_names
from server.app.services.workspace_package_clear_packed import (
    WorkspacePackageClearPackedMixin,
)
from server.app.services.workspace_package_contracts import (
    JobPackageItemResult,
    JobPackageResult,
)
from server.app.services.workspace_package_lifecycle import (
    WorkspacePackageLifecycleMixin,
    WorkspacePackageLockedError,  # noqa: F401
    WorkspacePackageNotFoundError,  # noqa: F401
)
from server.app.settings import Settings
from server.app.storage_paths import make_data_relative, resolve_job_dir


class JobPackageService(WorkspacePackageClearPackedMixin, WorkspacePackageLifecycleMixin):
    def __init__(self, job_db: JobQueries, settings: Settings) -> None:
        self.job_db = job_db
        self.settings = settings

    def list_workspace_packages(self, workspace_id: str, limit: int = 10) -> list[dict[str, Any]]:
        return self.job_db.list_workspace_packages(workspace_id, limit=limit)

    def package(
        self, workspace_id: str, job_ids: list[str] | None = None, **kwargs: Any
    ) -> JobPackageResult:
        """Package the selected jobs; kwargs take job_filter/exclude_ids."""
        ids = resolve_batch_selection(self.job_db, workspace_id, job_ids, **kwargs)
        if not ids:
            raise EmptyJobSelectionError("No job_ids provided or matched by the filter")
        results: list[JobPackageItemResult] = []
        eligible_jobs: list[dict[str, Any]] = []
        seen: set[str] = set()

        for job_id in ids:
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
        artifact_names = workspace_artifact_names(
            self.settings,
            {str(job.get("workflow_key", "")) for job in eligible_jobs},
            set(WORKSPACE_PACKAGE_FILES),
        )
        package_path, count = create_workspace_package(
            eligible_jobs,
            workspace_packages_dir,
            self.settings.jobs_dir,
            artifact_names=artifact_names,
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
        self._purge_source_videos(eligible_jobs)

        response["package_filename"] = package_filename
        response["download_url"] = download_url
        return response

    def _purge_source_videos(self, jobs: list[dict[str, Any]]) -> None:
        """Drop the original source.mp4 once packaged; preview falls back to source_url."""
        for job in jobs:
            source_path = resolve_job_dir(job, self.settings.jobs_dir) / "source.mp4"
            source_path.unlink(missing_ok=True)
