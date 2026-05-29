import atexit
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..db import Database
from ..events import VideoEventManager
from ..pipeline.package import create_package
from ..security import validate_package_filename
from ..services.video_actions import select_videos_for_package
from ..settings import Settings

_package_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="package-")
atexit.register(_package_executor.shutdown, wait=False)


class PackageRequest(BaseModel):
    video_ids: list[str] | None = None


class PackageResponse(BaseModel):
    accepted: bool


def create_packages_router(db: Database, settings: Settings, video_event_manager: VideoEventManager) -> APIRouter:
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
            package_path = create_package(selection.videos, settings.packages_dir, settings.videos_dir)
            video_ids = [v["id"] for v in selection.videos]
            db.batch_update_packed(video_ids, packed=1)
            download_url = f"/api/packages/{package_path.name}"
            video_event_manager.broadcast_package_ready(download_url)

        _package_executor.submit(_do_package)
        return {"accepted": True}

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

    return router
