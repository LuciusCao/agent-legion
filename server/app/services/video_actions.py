import shutil
from dataclasses import dataclass
from pathlib import Path

from server.app.db import Database
from server.app.pipeline.artifacts import clear_artifacts_from
from server.app.records import VideoRecord
from server.app.settings import Settings


@dataclass(frozen=True)
class PackageSelection:
    videos: list[VideoRecord]
    missing_ids: list[str]


def normalize_rerun_phase(video: VideoRecord, phase: str) -> str:
    if video["content_type"] == "question" and phase in {"interaction_generate", "content_review"}:
        return "assemble"
    return phase


def delete_video_record(db: Database, settings: Settings, video_id: str) -> bool:
    video = db.get_video(video_id)
    if not video:
        return False
    video_dir = Path(video["storage_dir"]) if video["storage_dir"] else settings.videos_dir / video_id
    if video_dir.exists() and video_dir.is_dir():
        shutil.rmtree(video_dir)
    db.delete_video(video_id)
    return True


def rerun_video_record(
    db: Database,
    settings: Settings,
    video_id: str,
    phase: str,
) -> dict[str, str]:
    video = db.get_video(video_id)
    if not video:
        return {
            "video_id": video_id,
            "status": "not_found",
            "phase": phase,
            "message": "Video not found",
        }

    normalized_phase = normalize_rerun_phase(video, phase)
    video_dir = Path(video["storage_dir"]) if video["storage_dir"] else settings.videos_dir / video_id
    try:
        clear_artifacts_from(video_dir, normalized_phase, video_id)
    except ValueError as exc:
        return {
            "video_id": video_id,
            "status": "invalid_phase",
            "phase": normalized_phase,
            "message": str(exc),
        }

    db.update_video(video_id, current_phase=normalized_phase, status="queued", error_message="")
    return {"video_id": video_id, "status": "rerun", "phase": normalized_phase, "message": ""}


def batch_delete_video_records(
    db: Database,
    settings: Settings,
    video_ids: list[str],
) -> list[dict[str, str]]:
    results = []
    for video_id in video_ids:
        if not delete_video_record(db, settings, video_id):
            results.append({"video_id": video_id, "status": "not_found", "message": "Video not found"})
            continue
        results.append({"video_id": video_id, "status": "deleted", "message": ""})
    return results


def batch_rerun_video_records(
    db: Database,
    settings: Settings,
    video_ids: list[str],
    phase: str,
) -> list[dict[str, str]]:
    return [rerun_video_record(db, settings, video_id, phase) for video_id in video_ids]


def select_videos_for_package(
    db: Database,
    video_ids: list[str] | None = None,
) -> PackageSelection:
    if video_ids:
        videos = []
        missing_ids = []
        for video_id in video_ids:
            video = db.get_video(video_id)
            if video:
                videos.append(video)
            else:
                missing_ids.append(video_id)
        return PackageSelection(videos=videos, missing_ids=missing_ids)

    completed = [video for video in db.list_videos() if video["status"] == "completed"]
    return PackageSelection(videos=completed or db.list_videos(), missing_ids=[])
