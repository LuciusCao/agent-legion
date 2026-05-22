from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from server.app.agents import AgentStatusManager
from server.app.db import Database
from server.app.pipeline.package import create_package
from server.app.pipeline.reader import read_artifacts
from server.app.services.intake import add_video_items
from server.app.services.video_actions import (
    batch_delete_video_records,
    batch_rerun_video_records,
    delete_video_record,
    rerun_video_record,
    select_videos_for_package,
)
from server.app.settings import Settings
from server.app.worker import process_next


class VideoInput(BaseModel):
    url: str = ""
    title: str = ""
    content_type: str = "knowledge"
    external_id: str = ""


class AddVideosRequest(BaseModel):
    items: list[VideoInput]


class RerunRequest(BaseModel):
    phase: str


class BatchVideoIdsRequest(BaseModel):
    video_ids: list[str]


class BatchRerunRequest(BatchVideoIdsRequest):
    phase: str


class PackageRequest(BaseModel):
    video_ids: list[str] | None = None


def create_router(db: Database, settings: Settings, agent_manager: AgentStatusManager) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @router.get("/agents")
    def list_agents() -> dict[str, Any]:
        return {"agents": agent_manager.to_dicts()}

    @router.websocket("/agents")
    async def agents_ws(websocket: WebSocket) -> None:
        await agent_manager.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except Exception:
            pass
        finally:
            agent_manager.disconnect(websocket)

    @router.post("/videos")
    def add_videos(request: AddVideosRequest) -> dict[str, Any]:
        return add_video_items(db, settings, request.items)

    @router.get("/videos")
    def list_videos() -> dict[str, Any]:
        return {"videos": db.list_videos()}

    @router.get("/videos/{video_id}")
    def get_video(video_id: str) -> dict[str, Any]:
        video = db.get_video(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        return {"video": video, "phase_runs": db.list_phase_runs(video_id)}

    @router.post("/videos/batch/delete")
    def batch_delete_videos(request: BatchVideoIdsRequest) -> dict[str, Any]:
        return {"results": batch_delete_video_records(db, settings, request.video_ids)}

    @router.post("/videos/batch/rerun")
    def batch_rerun_videos(request: BatchRerunRequest) -> dict[str, Any]:
        return {"results": batch_rerun_video_records(db, settings, request.video_ids, request.phase)}

    @router.post("/videos/{video_id}/rerun")
    def rerun_video(video_id: str, request: RerunRequest) -> dict[str, Any]:
        result = rerun_video_record(db, settings, video_id, request.phase)
        if result["status"] == "not_found":
            raise HTTPException(status_code=404, detail="Video not found")
        if result["status"] == "invalid_phase":
            raise HTTPException(status_code=400, detail=result["message"])
        return {"video": db.get_video(video_id)}

    @router.delete("/videos/{video_id}")
    def delete_video(video_id: str) -> dict[str, Any]:
        if not delete_video_record(db, settings, video_id):
            raise HTTPException(status_code=404, detail="Video not found")
        return {"deleted": True, "video_id": video_id}

    @router.get("/videos/{video_id}/artifacts")
    def artifacts(video_id: str) -> dict[str, Any]:
        video = db.get_video(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        video_dir = Path(video["storage_dir"]) if video["storage_dir"] else settings.videos_dir / video_id
        return read_artifacts(video_dir)

    @router.get("/videos/{video_id}/logs")
    def logs(video_id: str) -> dict[str, str]:
        runs = db.list_phase_runs(video_id)
        if not runs:
            return {"log": ""}
        log_path = Path(runs[-1]["log_path"])
        if not log_path.exists():
            return {"log": ""}
        return {"log": log_path.read_text(encoding="utf-8")[-8000:]}

    @router.get("/videos/{video_id}/video")
    def video_file(video_id: str):
        video = db.get_video(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        video_dir = Path(video["storage_dir"]) if video["storage_dir"] else settings.videos_dir / video_id
        path = video_dir / f"{video_id}.mp4"
        if not path.exists():
            return PlainTextResponse("Video not downloaded yet", status_code=404)
        return FileResponse(path, media_type="video/mp4")

    @router.post("/package")
    def package_completed(request: PackageRequest | None = None) -> dict[str, str]:
        requested_ids = request.video_ids if request and request.video_ids else None
        selection = select_videos_for_package(db, requested_ids)
        if selection.missing_ids:
            raise HTTPException(
                status_code=404,
                detail=f"Videos not found: {', '.join(selection.missing_ids)}",
            )
        package_path = create_package(selection.videos, settings.packages_dir)
        return {
            "path": str(package_path),
            "download_url": f"/api/packages/{package_path.name}",
        }

    @router.get("/packages/{filename:path}")
    def download_package(filename: str):
        package_path = settings.packages_dir / filename
        try:
            resolved = package_path.resolve()
            resolved.relative_to(settings.packages_dir.resolve())
        except (ValueError, RuntimeError):
            raise HTTPException(status_code=404, detail="Package not found") from None
        if not resolved.exists():
            raise HTTPException(status_code=404, detail="Package not found")
        return FileResponse(resolved, media_type="application/zip", filename=filename)

    @router.post("/worker/tick")
    def worker_tick() -> dict[str, bool]:
        return {"processed": process_next(db, settings)}

    return router
