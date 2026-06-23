import atexit
import json
import logging
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..db import Database
from ..events import VideoEventManager
from ..jobs import JobQueries
from ..pipeline.package import create_package
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
from ..services.video_actions import select_videos_for_package
from ..settings import Settings
from ..storage_paths import ManagedPathError, make_data_relative, resolve_data_path
from .job_operation_contracts import (
    WorkspacePackageRequest,
    WorkspacePackageResponse,
    WorkspacePackageResultResponse,
)

logger = logging.getLogger(__name__)

_package_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="package-")
atexit.register(_package_executor.shutdown, wait=False)


class PackageRequest(BaseModel):
    video_ids: list[str] | None = None
    name: str | None = None


class PackageUpdate(BaseModel):
    name: str | None = None
    locked: bool | None = None


class WorkspacePackageUpdate(BaseModel):
    name: str | None = None
    locked: bool | None = None


class WorkspacePackageDeleteResponse(BaseModel):
    deleted: bool


class WorkspacePackageUpdateResponse(BaseModel):
    id: int
    name: str | None = None
    locked: bool | None = None


class PackageResponse(BaseModel):
    accepted: bool


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

    @router.post("/package", response_model=PackageResponse)
    def package_completed(request: PackageRequest | None = None) -> dict[str, bool]:
        requested_ids = request.video_ids if request is not None else None
        selection = select_videos_for_package(db, requested_ids)
        if selection.missing_ids:
            raise HTTPException(
                status_code=404,
                detail=f"Videos not found: {', '.join(selection.missing_ids)}",
            )
        if selection.incomplete_ids:
            raise HTTPException(
                status_code=400,
                detail="No completed videos selected for packaging",
            )
        if request is not None and requested_ids == []:
            raise HTTPException(status_code=400, detail="No videos selected for packaging")
        if not selection.videos:
            raise HTTPException(
                status_code=400, detail="No completed videos available for packaging"
            )

        def _do_package() -> None:
            try:
                package_path, video_count = create_package(
                    selection.videos, settings.packages_dir, settings.videos_dir
                )
                size_bytes = package_path.stat().st_size
                name = (
                    request.name
                    if request is not None and request.name
                    else f"批次 ({video_count}个视频)"
                )
                relative_path = make_data_relative(package_path, settings.data_dir)
                db.insert_package(
                    relative_path, name=name, video_count=video_count, size_bytes=size_bytes
                )
                download_url = f"/api/packages/{package_path.name}"
                video_event_manager.broadcast_package_ready(download_url)
                video_ids = [v["id"] for v in selection.videos]
                db.batch_update_packed(video_ids, packed=1, notify=False)
            except Exception:
                logger.exception("Package creation failed")

        _package_executor.submit(_do_package)
        return {"accepted": True}

    @router.get("/packages")
    def list_packages() -> dict[str, Any]:
        packages = db.list_packages(limit=10)
        resolved_packages_dir = settings.packages_dir.resolve(strict=True)
        result: list[dict[str, Any]] = []
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
                if not pkg.get("name") or pkg.get("video_count", 0) == 0:
                    try:
                        with zipfile.ZipFile(resolved_path) as zf:
                            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                            video_count = len(manifest.get("videos", []))
                            name = pkg.get("name") or f"批次 ({video_count}个视频)"
                            size_bytes = pkg.get("size_bytes") or resolved_path.stat().st_size
                            db.update_package_stats(
                                pkg["id"],
                                name=name,
                                video_count=video_count,
                                size_bytes=size_bytes,
                            )
                            pkg_out["name"] = name
                            pkg_out["video_count"] = video_count
                            pkg_out["size_bytes"] = size_bytes
                    except Exception:
                        pass
                pkg_out["path"] = str(resolved_path)
            result.append(pkg_out)
        return {"packages": result}

    @router.delete("/packages/{package_id:int}")
    def delete_package(package_id: int) -> dict[str, bool]:
        try:
            package_deletion.delete(package_id)
        except (PackageNotFoundError, ManagedPathError):
            raise HTTPException(status_code=404, detail="Package not found") from None
        except PackageLockedError:
            raise HTTPException(status_code=400, detail="Package is locked") from None
        return {"deleted": True}

    @router.patch("/packages/{package_id:int}")
    def update_package(package_id: int, body: PackageUpdate) -> dict[str, Any]:
        packages = db.list_packages(limit=1000)
        target = next((p for p in packages if p["id"] == package_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="Package not found")
        if body.name is not None:
            db.update_package_name(package_id, body.name)
        if body.locked is not None:
            db.update_package_stats(package_id, locked=1 if body.locked else 0)
        result: dict[str, Any] = {"id": package_id}
        if body.name is not None:
            result["name"] = body.name
        if body.locked is not None:
            result["locked"] = body.locked
        return result

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

    @router.get("/workspaces/{workspace_id}/packages")
    def list_workspace_packages(workspace_id: str) -> dict[str, Any]:
        packages = job_packages.list_workspace_packages(workspace_id, limit=10)
        workspace_packages_dir = settings.packages_dir / f"workspace-{workspace_id}"
        result: list[dict[str, Any]] = []
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
            result.append(pkg_out)
        return {"packages": result}

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
