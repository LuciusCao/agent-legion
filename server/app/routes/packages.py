from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..db import Database
from ..events import VideoEventManager
from ..jobs import JobQueries
from ..security import validate_package_filename
from ..services.job_packages import (
    JobPackageService,
    WorkspacePackageLockedError,
    WorkspacePackageNotFoundError,
)
from ..services.package_deletion import (
    PackageDeletionService,
    PackageLockedError,
    PackageNotFoundError,
)
from ..settings import Settings
from ..storage_paths import ManagedPathError, resolve_data_path
from .package_contracts import (
    WorkspacePackageRequest,
    WorkspacePackageResponse,
    WorkspacePackageResultResponse,
)
from .package_history_contracts import (
    PackageDeleteResponse,
    PackageItemResponse,
    PackagesResponse,
    PackageUpdate,
    PackageUpdateResponse,
    WorkspacePackageDeleteResponse,
    WorkspacePackageItemResponse,
    WorkspacePackagesResponse,
    WorkspacePackageUpdate,
    WorkspacePackageUpdateResponse,
)


def create_packages_router(
    db: Database,
    job_db: JobQueries,
    settings: Settings,
    video_event_manager: VideoEventManager,
    package_deletion: PackageDeletionService,
    job_packages: JobPackageService | None = None,
) -> APIRouter:
    if job_packages is None:
        job_packages = JobPackageService(job_db, settings)
    router = APIRouter(tags=["packages"])

    @router.get("/packages", response_model=PackagesResponse)
    def list_packages() -> PackagesResponse:
        packages = db.list_packages(limit=10)
        resolved_packages_dir = settings.packages_dir.resolve(strict=True)
        result: list[PackageItemResponse] = []
        for pkg in packages:
            stored_path = pkg.get("path") or ""
            pkg_out = dict(pkg)
            if stored_path:
                try:
                    resolved_path = resolve_data_path(
                        stored_path, settings.data_dir, allow_missing=True
                    )
                    if resolved_path == resolved_packages_dir or not resolved_path.is_relative_to(
                        resolved_packages_dir
                    ):
                        raise ManagedPathError(
                            "Path escapes package root",
                            record_id=str(pkg.get("id", "")),
                            root_kind="package",
                        )
                except ManagedPathError:
                    continue
                pkg_out["path"] = str(resolved_path)
            result.append(PackageItemResponse.model_validate(pkg_out))
        return PackagesResponse(packages=result)

    @router.delete("/packages/{package_id:int}", response_model=PackageDeleteResponse)
    def delete_package(package_id: int) -> PackageDeleteResponse:
        try:
            package_deletion.delete(package_id)
        except (PackageNotFoundError, ManagedPathError):
            raise HTTPException(status_code=404, detail="Package not found") from None
        except PackageLockedError:
            raise HTTPException(status_code=400, detail="Package is locked") from None
        return PackageDeleteResponse(deleted=True)

    @router.patch(
        "/packages/{package_id:int}",
        response_model=PackageUpdateResponse,
        response_model_exclude_none=True,
    )
    def update_package(package_id: int, body: PackageUpdate) -> PackageUpdateResponse:
        packages = db.list_packages(limit=1000)
        target = next((p for p in packages if p["id"] == package_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="Package not found")
        if body.name is not None:
            db.update_package_name(package_id, body.name)
        if body.locked is not None:
            db.update_package_stats(package_id, locked=1 if body.locked else 0)
        return PackageUpdateResponse(id=package_id, name=body.name, locked=body.locked)

    @router.get(
        "/packages/{filename:path}",
        response_class=FileResponse,
        responses={200: {"content": {"application/zip": {}}}},
    )
    def download_package(filename: str) -> FileResponse:
        try:
            validate_package_filename(filename)
        except ValueError:
            raise HTTPException(status_code=404, detail="Package not found") from None
        package_path = settings.packages_dir / filename
        try:
            resolved = package_path.resolve()
            resolved.relative_to(settings.packages_dir.resolve())
        except (ValueError, RuntimeError):
            raise HTTPException(status_code=404, detail="Package not found") from None
        if not resolved.exists() or not resolved.is_file():
            raise HTTPException(status_code=404, detail="Package not found")
        return FileResponse(resolved, media_type="application/zip", filename=filename)

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

    @router.get(
        "/workspaces/{workspace_id}/packages/{filename:path}",
        response_class=FileResponse,
        responses={200: {"content": {"application/zip": {}}}},
    )
    def download_workspace_package(workspace_id: str, filename: str) -> FileResponse:
        try:
            validate_package_filename(filename)
        except ValueError:
            raise HTTPException(status_code=404, detail="Package not found") from None
        packages_dir = settings.packages_dir / f"workspace-{workspace_id}"
        package_path = packages_dir / filename
        try:
            resolved = package_path.resolve()
            resolved.relative_to(packages_dir.resolve())
        except (ValueError, RuntimeError):
            raise HTTPException(status_code=404, detail="Package not found") from None
        if not resolved.exists() or not resolved.is_file():
            raise HTTPException(status_code=404, detail="Package not found")
        return FileResponse(resolved, media_type="application/zip", filename=filename)

    return router
