from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.jobs import JobQueries
from server.app.settings import Settings
from server.app.storage_paths import resolve_data_path


class WorkspacePackageLifecycleError(Exception):
    pass


class WorkspacePackageLockedError(WorkspacePackageLifecycleError):
    pass


class WorkspacePackageNotFoundError(WorkspacePackageLifecycleError):
    pass


class WorkspacePackageLifecycleMixin:
    job_db: JobQueries
    settings: Settings

    def _resolve_workspace_package_path(
        self, workspace_id: str, package_id: int
    ) -> tuple[dict[str, Any], Path]:
        package = self.job_db.get_workspace_package(workspace_id, package_id)
        if package is None:
            raise WorkspacePackageNotFoundError(f"Package {package_id} not found")
        package_path = resolve_data_path(
            package["path"], self.settings.data_dir, allow_missing=True
        )
        packages_dir = self.settings.packages_dir / f"workspace-{workspace_id}"
        if not package_path.is_relative_to(packages_dir.resolve()):
            raise WorkspacePackageNotFoundError("Package path escapes workspace root")
        return package, package_path

    def rename_workspace_package(self, workspace_id: str, package_id: int, name: str) -> None:
        _, package_path = self._resolve_workspace_package_path(workspace_id, package_id)
        if not package_path.exists():
            raise WorkspacePackageNotFoundError("Package file missing")
        self.job_db.update_workspace_package_name(workspace_id, package_id, name)

    def lock_workspace_package(self, workspace_id: str, package_id: int, locked: bool) -> None:
        _, package_path = self._resolve_workspace_package_path(workspace_id, package_id)
        if not package_path.exists():
            raise WorkspacePackageNotFoundError("Package file missing")
        self.job_db.update_workspace_package_locked(workspace_id, package_id, 1 if locked else 0)

    def delete_workspace_package(self, workspace_id: str, package_id: int) -> None:
        package, package_path = self._resolve_workspace_package_path(workspace_id, package_id)
        if package["locked"]:
            raise WorkspacePackageLockedError("Package is locked")
        if package_path.exists():
            package_path.unlink()
        self.job_db.delete_workspace_package(workspace_id, package_id)
