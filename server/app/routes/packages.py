from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..jobs import JobQueries
from ..security import validate_package_filename
from ..services.job_packages import (
    JobPackageService,
    WorkspacePackageLockedError,
    WorkspacePackageNotFoundError,
)
from ..settings import Settings
from ..storage_paths import ManagedPathError, resolve_data_path, resolve_package_file
from .package_clear_packed import register_clear_packed_route
from .package_contracts import (
    WorkspacePackageRequest,
    WorkspacePackageResponse,
    WorkspacePackageResultResponse,
)
from .package_history_contracts import (
    WorkspacePackageDeleteResponse,
    WorkspacePackageItemResponse,
    WorkspacePackagesResponse,
    WorkspacePackageUpdate,
    WorkspacePackageUpdateResponse,
)


def create_packages_router(
    job_db: JobQueries,
    settings: Settings,
    job_packages: JobPackageService | None = None,
) -> APIRouter:
    if job_packages is None:
        job_packages = JobPackageService(job_db, settings)
    router = APIRouter(tags=["packages"])

    @router.get("/workspaces/{workspace_id}/packages", response_model=WorkspacePackagesResponse)
    def list_workspace_packages(workspace_id: str) -> WorkspacePackagesResponse:
        packages = job_packages.list_workspace_packages(workspace_id, limit=10)
        workspace_packages_dir = settings.packages_dir / f"workspace-{workspace_id}"
        result: list[WorkspacePackageItemResponse] = []
        for pkg in packages:
            stored_path = pkg.get("path") or ""
            pkg_out = dict(pkg)
            if "job_count" in pkg_out:
                pkg_out["video_count"] = pkg_out.pop("job_count")
            if stored_path:
                try:
                    resolved_path = resolve_data_path(
                        stored_path, settings.data_dir, allow_missing=True
                    )
                    if not resolved_path.is_relative_to(workspace_packages_dir.resolve()):
                        continue
                except ManagedPathError:
                    continue
                pkg_out["path"] = str(resolved_path)
            result.append(WorkspacePackageItemResponse.model_validate(pkg_out))
        return WorkspacePackagesResponse(packages=result)

    @router.delete(
        "/workspaces/{workspace_id}/packages/{package_id:int}",
        response_model=WorkspacePackageDeleteResponse,
    )
    def delete_workspace_package_route(
        workspace_id: str, package_id: int
    ) -> WorkspacePackageDeleteResponse:
        try:
            job_packages.delete_workspace_package(workspace_id, package_id)
        except WorkspacePackageNotFoundError:
            raise HTTPException(status_code=404, detail="Package not found") from None
        except WorkspacePackageLockedError:
            raise HTTPException(status_code=400, detail="Package is locked") from None
        return WorkspacePackageDeleteResponse(deleted=True)

    @router.patch(
        "/workspaces/{workspace_id}/packages/{package_id:int}",
        response_model=WorkspacePackageUpdateResponse,
    )
    def update_workspace_package_route(
        workspace_id: str, package_id: int, body: WorkspacePackageUpdate
    ) -> WorkspacePackageUpdateResponse:
        try:
            if body.name is not None:
                job_packages.rename_workspace_package(workspace_id, package_id, body.name)
            if body.locked is not None:
                job_packages.lock_workspace_package(workspace_id, package_id, body.locked)
        except WorkspacePackageNotFoundError:
            raise HTTPException(status_code=404, detail="Package not found") from None
        except WorkspacePackageLockedError:
            raise HTTPException(status_code=400, detail="Package is locked") from None

        return WorkspacePackageUpdateResponse(
            id=package_id,
            name=body.name if body.name is not None else None,
            locked=body.locked if body.locked is not None else None,
        )

    @router.post("/workspaces/{workspace_id}/jobs/package", response_model=WorkspacePackageResponse)
    def package_workspace_jobs(
        workspace_id: str, request: WorkspacePackageRequest
    ) -> WorkspacePackageResponse:
        if not request.job_ids:
            raise HTTPException(status_code=400, detail="No job_ids provided")
        package_result = job_packages.package(workspace_id, request.job_ids)
        return WorkspacePackageResponse(
            results=[
                WorkspacePackageResultResponse.model_validate(result)
                for result in package_result["results"]
            ],
            succeeded_count=package_result["succeeded_count"],
            failed_count=package_result["failed_count"],
            package_filename=package_result["package_filename"],
            download_url=package_result["download_url"],
        )

    register_clear_packed_route(router, job_packages)

    @router.get(
        "/workspaces/{workspace_id}/packages/{filename:path}",
        response_class=FileResponse,
        responses={200: {"content": {"application/zip": {}}}},
    )
    def download_workspace_package(workspace_id: str, filename: str) -> FileResponse:
        packages_dir = settings.packages_dir / f"workspace-{workspace_id}"
        try:
            validate_package_filename(filename)
            package_path = resolve_package_file(packages_dir, filename)
        except (ValueError, RuntimeError):
            raise HTTPException(status_code=404, detail="Package not found") from None
        if not package_path.exists() or not package_path.is_file():
            raise HTTPException(status_code=404, detail="Package not found")
        return FileResponse(package_path, media_type="application/zip", filename=filename)

    return router
