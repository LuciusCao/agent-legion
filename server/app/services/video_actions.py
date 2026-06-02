import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from server.app.agents import AgentStatusManager
from server.app.db import Database
from server.app.pipeline.artifacts import clear_artifacts_from
from server.app.pipeline.common import resolve_video_dir
from server.app.pipeline.phases import phase_sequence
from server.app.records import VideoRecord
from server.app.settings import Settings

FAILED_PHASE_RERUN_MARKER = "__failed__"


@dataclass(frozen=True)
class PackageSelection:
    videos: list[VideoRecord]
    missing_ids: list[str]
    incomplete_ids: list[str]


def normalize_rerun_phase(video: VideoRecord, phase: str) -> str:
    if video["content_type"] == "question" and phase in {"interaction_generate", "content_review"}:
        return "assemble"
    return phase


def can_rerun_from(video: VideoRecord, phase: str) -> bool:
    if video["status"] == "running":
        return False
    if video["status"] == "completed":
        return True
    phases = phase_sequence(video["content_type"])
    current = video["current_phase"]
    if current not in phases or phase not in phases:
        return False
    return phases.index(phase) <= phases.index(current)


def has_running_phase_run(db: Database, video_id: str) -> bool:
    return db.has_running_phase_run(video_id)


def delete_video_record(db: Database, settings: Settings, video_id: str) -> bool:
    video = db.get_video(video_id)
    if not video:
        return False
    video_dir = resolve_video_dir(video, settings.videos_dir)
    if video_dir.exists() and video_dir.is_dir():
        shutil.rmtree(video_dir)
    db.delete_video(video_id)
    return True


def rerun_video_record(
    db: Database,
    settings: Settings,
    video_id: str,
    phase: str,
    agent_manager: AgentStatusManager | None = None,
) -> dict[str, str]:
    video = db.get_video(video_id)
    if not video:
        return {
            "video_id": video_id,
            "status": "not_found",
            "phase": phase,
            "message": "Video not found",
        }

    if agent_manager is not None and agent_manager.is_video_busy(video_id):
        # Double-check against database: _busy_video_ids may hold stale
        # entries when the worker crashed before calling set_idle().
        if video["status"] == "running" or has_running_phase_run(db, video_id):
            return {
                "video_id": video_id,
                "status": "busy",
                "phase": phase,
                "message": "Video is currently being processed",
            }
        # Stale entry – clean it up and proceed with the rerun.
        agent_manager._busy_video_ids.discard(video_id)

    if video["status"] == "running" or has_running_phase_run(db, video_id):
        return {
            "video_id": video_id,
            "status": "busy",
            "phase": phase,
            "message": "Video is currently being processed",
        }

    if phase == FAILED_PHASE_RERUN_MARKER:
        phase = video["current_phase"]

    normalized_phase = normalize_rerun_phase(video, phase)
    if not can_rerun_from(video, normalized_phase):
        return {
            "video_id": video_id,
            "status": "skipped",
            "phase": normalized_phase,
            "message": f"当前处于 {video['current_phase']} 阶段，无法从 {normalized_phase} 重跑",
        }

    video_dir = resolve_video_dir(video, settings.videos_dir)

    if normalized_phase == "transcribe" and not (video_dir / f"{video_id}.mp4").exists():
        normalized_phase = "download"
    try:
        clear_artifacts_from(video_dir, normalized_phase, video_id)
    except ValueError as exc:
        return {
            "video_id": video_id,
            "status": "invalid_phase",
            "phase": normalized_phase,
            "message": str(exc),
        }

    phases = phase_sequence(video["content_type"])
    if phases.index(normalized_phase) <= phases.index("transcribe"):
        db.clear_transcription_runs(video_id)

    db.update_video(
        video_id, current_phase=normalized_phase, status="queued", error_message="", packed=0
    )
    return {"video_id": video_id, "status": "rerun", "phase": normalized_phase, "message": ""}


def _delete_video_dir(video: VideoRecord, settings: Settings) -> None:
    video_dir = resolve_video_dir(video, settings.videos_dir)
    if video_dir.exists() and video_dir.is_dir():
        shutil.rmtree(video_dir)


def batch_delete_video_records(
    db: Database,
    settings: Settings,
    video_ids: list[str],
) -> list[dict[str, str]]:
    videos = db.batch_get_videos(video_ids)
    found_map = {v["id"]: v for v in videos}

    # Parallel deletion of file system directories
    found_videos = [found_map[vid] for vid in video_ids if vid in found_map]
    with ThreadPoolExecutor(max_workers=8) as executor:
        executor.map(lambda v: _delete_video_dir(v, settings), found_videos)

    # Batch delete DB records
    found_ids = [vid for vid in video_ids if vid in found_map]
    if found_ids:
        db.batch_delete_videos(found_ids)

    results = []
    for video_id in video_ids:
        if video_id in found_map:
            results.append({"video_id": video_id, "status": "deleted", "message": ""})
        else:
            results.append(
                {"video_id": video_id, "status": "not_found", "message": "Video not found"}
            )
    return results


def batch_rerun_video_records(
    db: Database,
    settings: Settings,
    video_ids: list[str],
    phase: str,
    agent_manager: AgentStatusManager | None = None,
) -> list[dict[str, str]]:
    return [
        rerun_video_record(db, settings, video_id, phase, agent_manager) for video_id in video_ids
    ]


def select_videos_for_package(
    db: Database,
    video_ids: list[str] | None = None,
) -> PackageSelection:
    if video_ids is not None:
        videos = db.batch_get_videos(video_ids)
        found_ids = {v["id"] for v in videos}
        completed = [v for v in videos if v["status"] == "completed"]
        completed_ids = {v["id"] for v in completed}
        incomplete_ids = [vid for vid in video_ids if vid in found_ids and vid not in completed_ids]
        missing_ids = [vid for vid in video_ids if vid not in found_ids]
        return PackageSelection(
            videos=completed, missing_ids=missing_ids, incomplete_ids=incomplete_ids
        )

    completed = db.list_videos(status_filter="completed")
    return PackageSelection(videos=completed, missing_ids=[], incomplete_ids=[])
