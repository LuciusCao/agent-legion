import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from server.app.db import Database
from server.app.pipeline.artifacts import clear_artifacts_from
from server.app.pipeline.fetch_url import fetch_knowledge_url, fetch_question_url, get_token
from server.app.pipeline.package import create_package
from server.app.pipeline.reader import read_artifacts
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


def create_router(db: Database, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    def _try_fetch_url(item: VideoInput) -> str:
        if item.url:
            return item.url
        cms = settings.config.get("cms", {})
        if not cms or not item.external_id:
            return item.url
        try:
            env = cms.get("env", "prod")
            token = get_token(env, cms)
            if item.content_type == "knowledge":
                api_url = cms.get("knowledge_url")
                fetched = fetch_knowledge_url(item.external_id, api_url, token)
            else:
                api_url = cms.get("question_url")
                fetched = fetch_question_url(item.external_id, api_url, token)
        except Exception:
            return item.url
        return fetched or item.url

    @router.post("/videos")
    def add_videos(request: AddVideosRequest) -> dict[str, Any]:
        videos = []
        for item in request.items:
            url = _try_fetch_url(item)
            video = db.create_video(
                url,
                item.title,
                content_type=item.content_type,
                external_id=item.external_id,
            )
            video_dir = settings.videos_dir / video["id"]
            video_dir.mkdir(parents=True, exist_ok=True)
            status = "queued" if url else "missing_url"
            current_phase = "download" if url else "waiting_for_url"
            db.update_video(
                video["id"],
                storage_dir=str(video_dir),
                status=status,
                current_phase=current_phase,
            )
            videos.append(db.get_video(video["id"]))
        return {"videos": videos}

    @router.get("/videos")
    def list_videos() -> dict[str, Any]:
        return {"videos": db.list_videos()}

    @router.get("/videos/{video_id}")
    def get_video(video_id: str) -> dict[str, Any]:
        video = db.get_video(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        return {"video": video, "phase_runs": db.list_phase_runs(video_id)}

    @router.post("/videos/{video_id}/rerun")
    def rerun_video(video_id: str, request: RerunRequest) -> dict[str, Any]:
        video = db.get_video(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        video_dir = Path(video["storage_dir"]) if video["storage_dir"] else settings.videos_dir / video_id
        if video["content_type"] == "question" and request.phase in {
            "interaction_generate",
            "content_review",
        }:
            request.phase = "assemble"
        clear_artifacts_from(video_dir, request.phase, video_id)
        db.update_video(video_id, current_phase=request.phase, status="queued", error_message="")
        return {"video": db.get_video(video_id)}

    @router.delete("/videos/{video_id}")
    def delete_video(video_id: str) -> dict[str, Any]:
        video = db.get_video(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        video_dir = Path(video["storage_dir"]) if video["storage_dir"] else settings.videos_dir / video_id
        if video_dir.exists() and video_dir.is_dir():
            shutil.rmtree(video_dir)
        db.delete_video(video_id)
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
    def package_completed() -> dict[str, str]:
        videos = [video for video in db.list_videos() if video["status"] == "completed"]
        if not videos:
            videos = db.list_videos()
        package_path = create_package(videos, settings.packages_dir)
        return {"path": str(package_path)}

    @router.post("/worker/tick")
    def worker_tick() -> dict[str, bool]:
        return {"processed": process_next(db, settings)}

    return router
