import atexit
import json
import logging
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..db import Database
from ..events import VideoEventManager
from ..jobs import JobQueries
from ..pipeline.package import create_package
from ..security import validate_package_filename
from ..services.job_packages import JobPackageService
from ..services.video_actions import select_videos_for_package
from ..settings import Settings
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


class PackageResponse(BaseModel):
    accepted: bool


def create_packages_router(
    db: Database,
    job_db: JobQueries,
    settings: Settings,
    video_event_manager: VideoEventManager,
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
                db.insert_package(
                    str(package_path), name=name, video_count=video_count, size_bytes=size_bytes
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
        for pkg in packages:
            if not pkg.get("name") or pkg.get("video_count", 0) == 0:
                try:
                    with zipfile.ZipFile(pkg["path"]) as zf:
                        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                        video_count = len(manifest.get("videos", []))
                        name = pkg.get("name") or f"批次 ({video_count}个视频)"
                        size_bytes = pkg.get("size_bytes") or Path(pkg["path"]).stat().st_size
                        db.update_package_stats(
                            pkg["id"], name=name, video_count=video_count, size_bytes=size_bytes
                        )
                        pkg["name"] = name
                        pkg["video_count"] = video_count
                        pkg["size_bytes"] = size_bytes
                except Exception:
                    pass
        return {"packages": packages}

    @router.delete("/packages/{package_id:int}")
    def delete_package(package_id: int) -> dict[str, bool]:
        packages = db.list_packages(limit=1000)
        target = next((p for p in packages if p["id"] == package_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="Package not found")
        if target.get("locked"):
            raise HTTPException(status_code=400, detail="Package is locked")
        package_path = Path(target["path"])
        if package_path.exists():
            package_path.unlink()
        db.delete_package(package_id)
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

    @router.get("/packages/{filename:path}")
    def download_package(filename: str):
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
        packages_dir = settings.packages_dir / f"workspace-{workspace_id}"
        if not packages_dir.exists():
            return {"packages": []}
        packages = []
        for p in sorted(packages_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.suffix == ".zip":
                packages.append(
                    {
                        "id": p.name,
                        "name": p.stem,
                        "path": str(p),
                        "size_bytes": p.stat().st_size,
                        "created_at": datetime.fromtimestamp(p.stat().st_mtime, UTC).isoformat(),
                        "locked": 0,
                        "video_count": 0,
                    }
                )
        return {"packages": packages}

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

    @router.get("/workspaces/{workspace_id}/packages/{filename:path}")
    def download_workspace_package(workspace_id: str, filename: str):
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
