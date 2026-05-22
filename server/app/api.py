import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from server.app.agents import AgentStatusManager
from server.app.db import Database
from server.app.pipeline.artifacts import clear_artifacts_from
from server.app.pipeline.fetch_url import get_token, lookup_knowledge_video, lookup_question_video
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

    def _normalized_content_type(value: str) -> str:
        return value if value in {"knowledge", "question"} else "knowledge"

    def _resolve_video_input(item: VideoInput) -> tuple[str, str, str, str]:
        content_type = _normalized_content_type(item.content_type)
        external_id = item.external_id.strip()
        if item.url:
            return "created", item.url.strip(), item.title.strip(), ""
        if not external_id:
            return "invalid", "", "", "缺少资源 ID"

        cms = settings.config.get("cms", {})
        if not cms:
            return "fetch_failed", "", "", "CMS 配置缺失，无法校验资源是否存在"

        env = cms.get("env", "prod")
        token = get_token(env, cms)
        if content_type == "knowledge":
            lookup = lookup_knowledge_video(external_id, cms.get("knowledge_url"), token)
        else:
            lookup = lookup_question_video(external_id, cms.get("question_url"), token)

        if lookup.status == "not_found":
            return "not_found", "", "", "资源不存在"
        if lookup.status == "missing_url":
            return "created_missing_url", "", item.title.strip() or lookup.title, ""
        return "created", lookup.url, item.title.strip() or lookup.title, ""

    @router.post("/videos")
    def add_videos(request: AddVideosRequest) -> dict[str, Any]:
        videos = []
        results = []
        for item in request.items:
            content_type = _normalized_content_type(item.content_type)
            external_id = item.external_id.strip()

            if external_id:
                existing = db.find_video_by_identity(content_type, external_id)
                if existing:
                    results.append(
                        {
                            "external_id": external_id,
                            "content_type": content_type,
                            "status": "duplicate",
                            "message": "资源已在队列中",
                            "video": existing,
                        }
                    )
                    continue

            try:
                result_status, url, title, message = _resolve_video_input(item)
            except Exception as exc:
                results.append(
                    {
                        "external_id": external_id,
                        "content_type": content_type,
                        "status": "fetch_failed",
                        "message": str(exc),
                    }
                )
                continue

            if result_status in {"invalid", "not_found", "fetch_failed"}:
                results.append(
                    {
                        "external_id": external_id,
                        "content_type": content_type,
                        "status": result_status,
                        "message": message,
                    }
                )
                continue

            video = db.create_video(
                url,
                title,
                content_type=content_type,
                external_id=external_id,
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
            saved = db.get_video(video["id"])
            videos.append(saved)
            results.append(
                {
                    "external_id": external_id,
                    "content_type": content_type,
                    "status": result_status,
                    "message": "",
                    "video": saved,
                }
            )
        return {"videos": videos, "results": results}

    @router.get("/videos")
    def list_videos() -> dict[str, Any]:
        return {"videos": db.list_videos()}

    @router.get("/videos/{video_id}")
    def get_video(video_id: str) -> dict[str, Any]:
        video = db.get_video(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        return {"video": video, "phase_runs": db.list_phase_runs(video_id)}

    def _normalize_rerun_phase(video: dict[str, Any], phase: str) -> str:
        if video["content_type"] == "question" and phase in {"interaction_generate", "content_review"}:
            return "assemble"
        return phase

    def _delete_video_record(video_id: str) -> bool:
        video = db.get_video(video_id)
        if not video:
            return False
        video_dir = Path(video["storage_dir"]) if video["storage_dir"] else settings.videos_dir / video_id
        if video_dir.exists() and video_dir.is_dir():
            shutil.rmtree(video_dir)
        db.delete_video(video_id)
        return True

    @router.post("/videos/batch/delete")
    def batch_delete_videos(request: BatchVideoIdsRequest) -> dict[str, Any]:
        results = []
        for video_id in request.video_ids:
            if not _delete_video_record(video_id):
                results.append(
                    {"video_id": video_id, "status": "not_found", "message": "Video not found"}
                )
                continue
            results.append({"video_id": video_id, "status": "deleted", "message": ""})
        return {"results": results}

    @router.post("/videos/batch/rerun")
    def batch_rerun_videos(request: BatchRerunRequest) -> dict[str, Any]:
        results = []
        for video_id in request.video_ids:
            video = db.get_video(video_id)
            if not video:
                results.append(
                    {
                        "video_id": video_id,
                        "status": "not_found",
                        "phase": request.phase,
                        "message": "Video not found",
                    }
                )
                continue
            phase = _normalize_rerun_phase(video, request.phase)
            video_dir = Path(video["storage_dir"]) if video["storage_dir"] else settings.videos_dir / video_id
            try:
                clear_artifacts_from(video_dir, phase, video_id)
            except ValueError as exc:
                results.append(
                    {
                        "video_id": video_id,
                        "status": "invalid_phase",
                        "phase": phase,
                        "message": str(exc),
                    }
                )
                continue
            db.update_video(video_id, current_phase=phase, status="queued", error_message="")
            results.append({"video_id": video_id, "status": "rerun", "phase": phase, "message": ""})
        return {"results": results}

    @router.post("/videos/{video_id}/rerun")
    def rerun_video(video_id: str, request: RerunRequest) -> dict[str, Any]:
        video = db.get_video(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        phase = _normalize_rerun_phase(video, request.phase)
        video_dir = Path(video["storage_dir"]) if video["storage_dir"] else settings.videos_dir / video_id
        try:
            clear_artifacts_from(video_dir, phase, video_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.update_video(video_id, current_phase=phase, status="queued", error_message="")
        return {"video": db.get_video(video_id)}

    @router.delete("/videos/{video_id}")
    def delete_video(video_id: str) -> dict[str, Any]:
        if not _delete_video_record(video_id):
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
        if requested_ids:
            videos = [video for video_id in requested_ids if (video := db.get_video(video_id))]
        else:
            videos = [video for video in db.list_videos() if video["status"] == "completed"]
            if not videos:
                videos = db.list_videos()
        package_path = create_package(videos, settings.packages_dir)
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
