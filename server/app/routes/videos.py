import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from ..agents import AgentStatusManager
from ..db import Database
from ..events import VideoEventManager
from ..pipeline.common import resolve_video_dir
from ..pipeline.openclaw_sessions import render_openclaw_session, resolve_openclaw_session_path
from ..services.intake import add_video_items
from ..services.manual_run import (
    batch_submit_run_to_phase,
    submit_run_to_phase,
)
from ..services.video_actions import (
    batch_delete_video_records,
    batch_rerun_video_records,
    delete_video_record,
    rerun_video_record,
)
from ..services.video_read import VideoReadService
from ..settings import Settings


class VideoInput(BaseModel):
    url: str = ""
    title: str = ""
    content_type: str = "knowledge"
    external_id: str = ""
    source_uuid: str = ""


class AddVideosRequest(BaseModel):
    items: list[VideoInput]


class RerunRequest(BaseModel):
    phase: str


class RunToRequest(BaseModel):
    target_phase: str
    start_phase: str | None = None


class BatchVideoIdsRequest(BaseModel):
    video_ids: list[str]


class BatchRerunRequest(BatchVideoIdsRequest):
    phase: str


class BatchRunToRequest(BatchVideoIdsRequest):
    target_phase: str
    start_phase: str | None = None


class DeleteResult(BaseModel):
    video_id: str
    status: str
    message: str


class BatchDeleteResponse(BaseModel):
    results: list[DeleteResult]


class RerunResult(DeleteResult):
    phase: str


class RunToResult(DeleteResult):
    phase: str


class BatchRerunResponse(BaseModel):
    results: list[RerunResult]


class RunToSingleResponse(BaseModel):
    result: RunToResult
    video: dict[str, Any] | None


class BatchRunToResponse(BaseModel):
    results: list[RunToResult]


def create_videos_router(
    db: Database,
    settings: Settings,
    agent_manager: AgentStatusManager,
    video_event_manager: VideoEventManager,
) -> APIRouter:
    router = APIRouter(prefix="/videos", tags=["videos"])

    @router.get("/events")
    async def videos_events(request: Request):
        return await video_event_manager.connect(request)

    @router.get("/{video_id}/events")
    async def video_detail_events(request: Request, video_id: str):
        return await video_event_manager.connect_video(request, video_id)

    @router.post("")
    def add_videos(request: AddVideosRequest) -> dict[str, Any]:
        return add_video_items(db, settings, request.items)

    read_service = VideoReadService(db, settings)

    @router.get("")
    def list_videos() -> dict[str, Any]:
        videos = read_service.list_videos()
        for video in videos:
            video["packed"] = bool(video.get("packed", 0))
        return {"videos": videos}

    @router.get("/{video_id}")
    def get_video(video_id: str) -> dict[str, Any]:
        video = read_service.get_video_detail(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        return {
            "video": {**video, "packed": bool(video.get("packed", 0))},
            "phase_runs": db.list_phase_runs(video_id),
            "transcription_runs": db.list_transcription_runs(video_id),
        }

    @router.post("/batch/delete", response_model=BatchDeleteResponse)
    def batch_delete_videos(request: BatchVideoIdsRequest) -> dict[str, Any]:
        return {"results": batch_delete_video_records(db, settings, request.video_ids)}

    @router.post("/batch/rerun", response_model=BatchRerunResponse)
    def batch_rerun_videos(request: BatchRerunRequest) -> dict[str, Any]:
        return {
            "results": batch_rerun_video_records(
                db, settings, request.video_ids, request.phase, agent_manager
            )
        }

    @router.post("/batch/run-to", response_model=BatchRunToResponse)
    def batch_run_to_videos(request: BatchRunToRequest) -> dict[str, Any]:
        return {
            "results": batch_submit_run_to_phase(
                db,
                settings,
                request.video_ids,
                target_phase=request.target_phase,
                start_phase=request.start_phase,
                agent_manager=agent_manager,
            )
        }

    @router.post("/{video_id}/run-to", response_model=RunToSingleResponse)
    def run_video_to_phase(video_id: str, request: RunToRequest) -> dict[str, Any]:
        result = submit_run_to_phase(
            db,
            settings,
            video_id,
            target_phase=request.target_phase,
            start_phase=request.start_phase,
            agent_manager=agent_manager,
        )
        if result["status"] == "not_found":
            raise HTTPException(status_code=404, detail="Video not found")
        if result["status"] == "busy":
            raise HTTPException(status_code=409, detail=result["message"])
        if result["status"] == "invalid_phase":
            raise HTTPException(status_code=400, detail=result["message"])
        return {"result": result, "video": db.get_video(video_id)}

    @router.post("/{video_id}/rerun")
    def rerun_video(video_id: str, request: RerunRequest) -> dict[str, Any]:
        result = rerun_video_record(db, settings, video_id, request.phase, agent_manager)
        if result["status"] == "not_found":
            raise HTTPException(status_code=404, detail="Video not found")
        if result["status"] == "busy":
            raise HTTPException(status_code=409, detail=result["message"])
        if result["status"] == "invalid_phase":
            raise HTTPException(status_code=400, detail=result["message"])
        return {"video": db.get_video(video_id)}

    @router.delete("/{video_id}")
    def delete_video(video_id: str) -> dict[str, Any]:
        if not delete_video_record(db, settings, video_id):
            raise HTTPException(status_code=404, detail="Video not found")
        return {"deleted": True, "video_id": video_id}

    def _sanitize_log(text: str) -> str:
        """Filter potentially sensitive paths and URLs from log output."""
        # Remove absolute file paths (Unix and Windows)
        text = re.sub(r"/[\w./-]+/\.[\w./-]+", "[FILTERED]", text)
        text = re.sub(r"[A-Za-z]:\\[\\\w\s.-]+", "[FILTERED]", text)
        # Remove URLs with potential credentials or sensitive paths
        text = re.sub(r"https?://[^\s]+", "[FILTERED]", text)
        return text

    @router.get("/{video_id}/logs")
    def logs(video_id: str) -> dict[str, str]:
        runs = db.list_phase_runs(video_id)
        if not runs:
            return {"log": ""}
        log_path = Path(runs[-1]["log_path"])
        if not log_path.exists():
            return {"log": ""}
        with log_path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 12000), 0)
            tail = f.read().decode("utf-8", errors="ignore")
        return {"log": _sanitize_log(tail[-8000:])}

    @router.get("/{video_id}/phase-runs/{run_id}/session")
    def phase_run_session(video_id: str, run_id: int) -> dict[str, str]:
        run = db.get_phase_run(video_id, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Phase run not found")
        session_id = run.get("agent_session_id") or ""
        path = resolve_openclaw_session_path(run.get("agent_id") or "", session_id)
        if not path:
            raise HTTPException(status_code=404, detail="OpenClaw session not found")
        return {"session_id": session_id, "log": render_openclaw_session(path)}

    @router.get("/{video_id}/video")
    @router.head("/{video_id}/video")
    def video_file(video_id: str):
        video = db.get_video(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        video_dir = resolve_video_dir(video, settings.videos_dir)
        path = video_dir / f"{video_id}.mp4"
        if path.exists():
            return FileResponse(path, media_type="video/mp4")
        source_url = video.get("source_url", "")
        if source_url:
            return RedirectResponse(source_url, status_code=302)
        return PlainTextResponse("Video not downloaded yet", status_code=404)

    return router
