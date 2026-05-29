from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..db import Database
from ..pipeline.package import create_package
from ..services.video_actions import select_videos_for_package
from ..settings import Settings


class PackageRequest(BaseModel):
    video_ids: list[str] | None = None


class PackageResponse(BaseModel):
    path: str
    download_url: str


def create_packages_router(db: Database, settings: Settings) -> APIRouter:
    router = APIRouter(tags=["packages"])

    @router.post("/package", response_model=PackageResponse)
    def package_completed(request: PackageRequest | None = None) -> dict[str, str]:
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
        package_path = create_package(selection.videos, settings.packages_dir, settings.videos_dir)
        for video in selection.videos:
            db.update_video(video["id"], packed=1)
        return {
            "path": str(package_path),
            "download_url": f"/api/packages/{package_path.name}",
        }

    @router.get("/packages/{filename:path}")
    def download_package(filename: str):
        # Reject empty filenames and path traversal attempts
        if not filename or filename.startswith("/") or ".." in Path(filename).parts:
            raise HTTPException(status_code=404, detail="Package not found")
        package_path = settings.packages_dir / filename
        try:
            resolved = package_path.resolve()
            resolved.relative_to(settings.packages_dir.resolve())
        except (ValueError, RuntimeError):
            raise HTTPException(status_code=404, detail="Package not found") from None
        if not resolved.exists() or not resolved.is_file():
            raise HTTPException(status_code=404, detail="Package not found")
        return FileResponse(resolved, media_type="application/zip", filename=filename)

    return router
