from server.app.agents import AgentStatusManager
from server.app.db import Database
from server.app.pipeline.artifacts import clear_artifacts_from
from server.app.pipeline.common import resolve_video_dir
from server.app.pipeline.openclaw import OpenClawRunner
from server.app.pipeline.phases import phase_sequence
from server.app.pipeline.transcribe import TranscriptionProvider
from server.app.records import VideoRecord
from server.app.services.video_actions import has_running_phase_run
from server.app.settings import Settings
from server.app.worker import process_video_once


def _result(video_id: str, status: str, phase: str, message: str = "") -> dict[str, str]:
    return {"video_id": video_id, "status": status, "phase": phase, "message": message}


def _invalid_phase(video_id: str, phase: str, message: str) -> dict[str, str]:
    return _result(video_id, "invalid_phase", phase, message)


def _is_busy(
    db: Database,
    video: VideoRecord,
    agent_manager: AgentStatusManager | None,
) -> bool:
    return (
        agent_manager is not None
        and agent_manager.is_video_busy(video["id"])
        or video["status"] == "running"
        or has_running_phase_run(db, video["id"])
    )


def _phase_index(phases: list[str], phase: str) -> int:
    return phases.index(phase)


def _validate_phase(video: VideoRecord, phase: str, role: str) -> str | None:
    phases = phase_sequence(video["content_type"])
    if phase not in phases:
        return f"{role} {phase} 不适用于该视频类型"
    return None


def _is_waiting_for_url(video: VideoRecord) -> bool:
    return video["status"] == "missing_url" and video["current_phase"] == "waiting_for_url"


def _missing_url_result(video_id: str, target_phase: str, video: VideoRecord) -> dict[str, str]:
    message = video.get("error_message") or "No source URL available"
    status = "failed" if video.get("error_message") else "skipped"
    return _result(video_id, status, target_phase, message)


def _prepare_rerun(
    db: Database,
    settings: Settings,
    video: VideoRecord,
    start_phase: str,
) -> tuple[str, str]:
    video_id = video["id"]
    video_dir = resolve_video_dir(video, settings.videos_dir)
    normalized_start = start_phase
    if normalized_start == "transcribe" and not (video_dir / f"{video_id}.mp4").exists():
        normalized_start = "download"
    clear_artifacts_from(video_dir, normalized_start, video_id)
    phases = phase_sequence(video["content_type"])
    if phases.index(normalized_start) <= phases.index("transcribe"):
        db.clear_transcription_runs(video_id)
    db.update_video(
        video_id,
        current_phase=normalized_start,
        status="queued",
        error_message="",
        packed=0,
    )
    return normalized_start, ""


def _run_loop(
    db: Database,
    settings: Settings,
    video_id: str,
    target_phase: str,
    mode_status: str,
    providers: list[TranscriptionProvider] | None,
    openclaw_runner: OpenClawRunner | None,
) -> dict[str, str]:
    while True:
        video = db.get_video(video_id)
        if not video:
            return _result(video_id, "not_found", target_phase, "Video not found")
        if video["status"] in {"failed", "completed"}:
            if video["status"] == "failed":
                return _result(video_id, "failed", target_phase, video.get("error_message", ""))
            return _result(video_id, mode_status, target_phase)
        if video["status"] == "missing_url" and not _is_waiting_for_url(video):
            return _missing_url_result(video_id, target_phase, video)

        phases = phase_sequence(video["content_type"])
        current_phase = video["current_phase"]
        if not _is_waiting_for_url(video) and current_phase not in phases:
            return _invalid_phase(video_id, target_phase, f"当前阶段 {current_phase} 不适用于该视频类型")
        if not _is_waiting_for_url(video) and _phase_index(phases, current_phase) > _phase_index(phases, target_phase):
            return _result(video_id, mode_status, target_phase)

        processed = process_video_once(
            db,
            settings,
            video_id,
            providers=providers,
            openclaw_runner=openclaw_runner,
            stop_after_phase=target_phase,
        )
        if not processed:
            updated = db.get_video(video_id)
            if updated and updated["status"] == "missing_url":
                return _missing_url_result(video_id, target_phase, updated)
            if updated and (updated["status"] == "running" or has_running_phase_run(db, video_id)):
                return _result(video_id, "busy", target_phase, "Video is currently being processed")
            message = updated.get("error_message", "") if updated else "Video not found"
            return _result(video_id, "failed", target_phase, message)


def run_to_phase(
    db: Database,
    settings: Settings,
    video_id: str,
    *,
    target_phase: str,
    start_phase: str | None = None,
    providers: list[TranscriptionProvider] | None = None,
    openclaw_runner: OpenClawRunner | None = None,
    agent_manager: AgentStatusManager | None = None,
) -> dict[str, str]:
    video = db.get_video(video_id)
    if not video:
        return _result(video_id, "not_found", target_phase, "Video not found")

    if _is_busy(db, video, agent_manager):
        return _result(video_id, "busy", target_phase, "Video is currently being processed")

    phases = phase_sequence(video["content_type"])
    target_error = _validate_phase(video, target_phase, "目标阶段")
    if target_error:
        return _invalid_phase(video_id, target_phase, target_error)

    target_index = _phase_index(phases, target_phase)
    mode_status = "run_to"
    if start_phase is None:
        current_phase = video["current_phase"]
        if not _is_waiting_for_url(video):
            current_error = _validate_phase(video, current_phase, "当前阶段")
            if current_error:
                return _invalid_phase(video_id, target_phase, current_error)
            if target_index < _phase_index(phases, current_phase):
                return _invalid_phase(video_id, target_phase, "目标阶段早于当前阶段，无法继续执行")
    else:
        start_error = _validate_phase(video, start_phase, "起始阶段")
        if start_error:
            return _invalid_phase(video_id, target_phase, start_error)
        if _phase_index(phases, start_phase) > target_index:
            return _invalid_phase(video_id, target_phase, "起始阶段晚于目标阶段，无法重跑")
        mode_status = "rerun_to"
        try:
            normalized_start, _ = _prepare_rerun(db, settings, video, start_phase)
        except ValueError as exc:
            return _invalid_phase(video_id, target_phase, str(exc))
        if _phase_index(phases, normalized_start) > target_index:
            return _invalid_phase(video_id, target_phase, "起始阶段晚于目标阶段，无法重跑")

    return _run_loop(
        db,
        settings,
        video_id,
        target_phase,
        mode_status,
        providers,
        openclaw_runner,
    )


def batch_run_to_phase(
    db: Database,
    settings: Settings,
    video_ids: list[str],
    *,
    target_phase: str,
    start_phase: str | None = None,
    providers: list[TranscriptionProvider] | None = None,
    openclaw_runner: OpenClawRunner | None = None,
    agent_manager: AgentStatusManager | None = None,
) -> list[dict[str, str]]:
    results = []
    for video_id in video_ids:
        video = db.get_video(video_id)
        if video:
            target_error = _validate_phase(video, target_phase, "目标阶段")
            start_error = _validate_phase(video, start_phase, "起始阶段") if start_phase else None
            if target_error or start_error:
                results.append(_result(video_id, "skipped", target_phase, target_error or start_error or ""))
                continue
        results.append(
            run_to_phase(
                db,
                settings,
                video_id,
                target_phase=target_phase,
                start_phase=start_phase,
                providers=providers,
                openclaw_runner=openclaw_runner,
                agent_manager=agent_manager,
            )
        )
    return results
