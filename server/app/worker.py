import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from server.app.db import Database
from server.app.pipeline.assemble import assemble_video
from server.app.pipeline.common import resolve_video_dir
from server.app.pipeline.download import download_video
from server.app.pipeline.fetch_url import get_token, lookup_knowledge_video, lookup_question_video
from server.app.pipeline.openclaw import OpenClawRunner
from server.app.pipeline.phases import AGENT_PHASES, next_phase
from server.app.pipeline.runners import build_openclaw_runner
from server.app.pipeline.transcribe import (
    SenseVoiceProvider,
    TranscriptionProvider,
    WhisperCppProvider,
    run_transcription_with_providers,
)
from server.app.pipeline.validators import validate_phase_outputs
from server.app.settings import Settings

DEFAULT_PHASE_CONCURRENCY = {
    "download": 10,
    "transcribe": 2,
    "assemble": 10,
    "waiting_for_url": 10,
}


@dataclass(frozen=True)
class WorkerCapacity:
    free_runner: Any | None
    running_local_counts: dict[str, int]


@dataclass(frozen=True)
class WorkItem:
    kind: str
    video: dict[str, Any]
    phase: str


def phase_requires_openclaw(phase: str) -> bool:
    return phase in AGENT_PHASES


def get_phase_concurrency_limit(settings: Settings, phase: str) -> int:
    worker_config = settings.config.get("worker", {})
    phase_config = worker_config.get("phase_concurrency", {})
    configured = phase_config.get(phase, DEFAULT_PHASE_CONCURRENCY.get(phase, 1))
    try:
        return max(1, int(configured))
    except (TypeError, ValueError):
        return DEFAULT_PHASE_CONCURRENCY.get(phase, 1)


def pick_next_work(
    videos: list[dict[str, Any]],
    running_video_ids: set[str],
    capacity: WorkerCapacity,
    settings: Settings,
) -> WorkItem | None:
    for video in videos:
        if video["status"] not in {"queued", "missing_url"}:
            continue
        if video["id"] in running_video_ids:
            continue
        phase = video["current_phase"]
        if phase_requires_openclaw(phase):
            if capacity.free_runner is not None:
                return WorkItem(kind="agent", video=video, phase=phase)
            continue
        limit = get_phase_concurrency_limit(settings, phase)
        if capacity.running_local_counts.get(phase, 0) < limit:
            return WorkItem(kind="local", video=video, phase=phase)
    return None


def expected_outputs_exist(video_dir: Path, output_names: list[str]) -> bool:
    return all((video_dir / name).exists() for name in output_names)


def phase_outputs_sufficient(video_dir: Path, phase_key: str, output_names: list[str]) -> bool:
    if phase_key == "chapter_generate":
        return (video_dir / "chapters.json").exists()
    if phase_key == "subtitle_review":
        return (video_dir / "subtitles_reviewed.srt").exists()
    return expected_outputs_exist(video_dir, output_names)



def build_default_providers(settings: Settings) -> list[TranscriptionProvider]:
    asr = settings.config.get("asr", {})
    whisper = asr.get("whisper", {})
    sensevoice = asr.get("sensevoice", {})
    providers: list[TranscriptionProvider] = [
        WhisperCppProvider(
            binary=str(whisper.get("binary", "")),
            model=str(whisper.get("model", "")),
        ),
        SenseVoiceProvider(
            script=str(
                settings.root_dir / str(sensevoice.get("script", "scripts/sensevoice_srt.py"))
            ),
            model_dir=str(
                settings.root_dir
                / str(sensevoice.get("model_dir", "models/SenseVoiceSmall"))
            ),
        ),
    ]
    return providers


def process_video_once(
    db: Database,
    settings: Settings,
    video_id: str,
    providers: list[TranscriptionProvider] | None = None,
    openclaw_runner: OpenClawRunner | None = None,
) -> bool:
    video = db.get_video(video_id)
    if not video or video["status"] not in {"queued", "running", "missing_url"}:
        return False

    phase = video["current_phase"]
    if phase == "waiting_for_url" or not video["source_url"]:
        cms = settings.config.get("cms", {})
        fetched_url = ""
        fetched_source_uuid = ""
        fetch_error = ""
        try:
            if cms and video.get("external_id"):
                env = cms.get("env", "prod")
                token = get_token(env, cms)
                if video.get("content_type") == "knowledge":
                    api_url = cms.get("knowledge_url")
                    lookup = lookup_knowledge_video(video["external_id"], api_url, token)
                else:
                    api_url = cms.get("question_url")
                    lookup = lookup_question_video(video["external_id"], api_url, token)
                fetched_url = lookup.url or ""
                fetched_source_uuid = lookup.source_uuid or ""
        except Exception as exc:
            fetched_url = ""
            fetch_error = f"fetch url failed: {exc}"
        if fetched_url:
            update_fields: dict[str, Any] = {
                "source_url": fetched_url,
                "status": "queued",
                "current_phase": "download",
                "error_message": "",
            }
            if fetched_source_uuid:
                update_fields["source_uuid"] = fetched_source_uuid
            db.update_video(video_id, **update_fields)
            video = db.get_video(video_id)
            phase = video["current_phase"]
        else:
            if not fetch_error and cms and video.get("external_id"):
                fetch_error = "fetch url failed: CMS did not return a video URL"
            update_fields: dict[str, Any] = {
                "status": "missing_url",
                "current_phase": "waiting_for_url",
                "error_message": fetch_error,
            }
            if fetched_source_uuid:
                update_fields["source_uuid"] = fetched_source_uuid
            db.update_video(video_id, **update_fields)
            return False
    video_dir = resolve_video_dir(video, settings.videos_dir)
    video_dir.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_dir / f"{video_id}-{phase}.log"
    run = db.start_phase(video_id, phase, [], str(log_path))
    if run is None:
        return False

    try:
        if phase == "download":
            download_video(video["source_url"], video_dir / f"{video_id}.mp4")
        elif phase == "transcribe":
            active_providers = providers or build_default_providers(settings)
            result = run_transcription_with_providers(
                video_path=video_dir / f"{video_id}.mp4",
                output_dir=video_dir,
                title=video["title"],
                duration=float(video.get("duration") or 0),
                mode=str(settings.config.get("asr", {}).get("provider", "auto")),
                providers=active_providers,
            )
            log_path.write_text(
                json.dumps(result.__dict__, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        elif phase in AGENT_PHASES:
            agent_phase = AGENT_PHASES[phase]
            if not phase_outputs_sufficient(video_dir, phase, agent_phase.expected_outputs):
                runner = openclaw_runner or build_openclaw_runner(settings)
                result = runner.run(
                    phase=agent_phase,
                    video_id=video_id,
                    video_dir=video_dir,
                    prompt_dir=settings.data_dir / "prompts",
                    log_path=log_path,
                )
                if getattr(result, "command", None):
                    db.update_phase_command(run["id"], result.command)
                if result.status != "completed":
                    raise RuntimeError(result.error_message)
                validate_phase_outputs(video_dir, phase)
        elif phase == "assemble":
            assemble_video(video, video_dir)
        else:
            raise ValueError(f"Unknown phase: {phase}")
    except Exception as exc:
        if not log_path.exists():
            log_path.write_text(str(exc), encoding="utf-8")
        db.finish_phase(run["id"], "failed", 1, str(exc))
        return True

    following = next_phase(phase, video.get("content_type", "knowledge"))
    db.finish_phase(run["id"], "completed", 0, "")
    if following is None:
        db.update_video(video_id, current_phase=phase, status="completed", error_message="")
    else:
        db.update_video(video_id, current_phase=following, status="queued", error_message="")

    if phase == "assemble" and settings.config.get("cleanup_video_after_assemble", False):
        mp4_path = video_dir / f"{video_id}.mp4"
        if mp4_path.exists():
            try:
                mp4_path.unlink()
            except OSError as exc:
                if log_path.exists():
                    existing = log_path.read_text(encoding="utf-8")
                    log_path.write_text(f"{existing}\nCleanup warning: {exc}", encoding="utf-8")

    return True


def process_next(
    db: Database,
    settings: Settings,
    openclaw_runner: OpenClawRunner | None = None,
) -> bool:
    for video in db.list_videos():
        if (
            openclaw_runner is None
            and video["status"] == "queued"
            and phase_requires_openclaw(video["current_phase"])
        ):
            continue
        if video["status"] in {"queued", "missing_url"} and process_video_once(
            db, settings, video["id"], openclaw_runner=openclaw_runner
        ):
            return True
    return False
