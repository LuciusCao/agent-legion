import json
from pathlib import Path

from server.app.db import Database
from server.app.pipeline.assemble import assemble_video
from server.app.pipeline.download import download_video
from server.app.pipeline.openclaw import OpenClawRunner
from server.app.pipeline.phases import AGENT_PHASES, next_phase
from server.app.pipeline.transcribe import (
    SenseVoiceProvider,
    TranscriptionProvider,
    WhisperCppProvider,
    run_transcription_with_providers,
)
from server.app.settings import Settings


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
            script=str(settings.root_dir / str(sensevoice.get("script", "scripts/sensevoice_srt.py"))),
            model_dir=str(settings.root_dir / str(sensevoice.get("model_dir", "models/SenseVoiceSmall"))),
        ),
    ]
    return providers


def build_openclaw_runner(settings: Settings) -> OpenClawRunner:
    openclaw = settings.config.get("openclaw", {})
    return OpenClawRunner(
        command_template=list(openclaw.get("command_template", ["openclaw", "run", "--prompt-file", "{prompt_file}"])),
        cwd=(settings.root_dir / str(openclaw.get("cwd", "."))).resolve(),
        timeout_seconds=int(openclaw.get("timeout_seconds", 600)),
    )


def process_video_once(
    db: Database,
    settings: Settings,
    video_id: str,
    providers: list[TranscriptionProvider] | None = None,
    openclaw_runner: OpenClawRunner | None = None,
) -> bool:
    video = db.get_video(video_id)
    if not video or video["status"] not in {"queued", "running"}:
        return False

    phase = video["current_phase"]
    if phase == "waiting_for_url" or not video["source_url"]:
        db.update_video(video_id, status="missing_url", current_phase="waiting_for_url")
        return False
    video_dir = Path(video["storage_dir"]) if video["storage_dir"] else settings.videos_dir / video_id
    video_dir.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_dir / f"{video_id}-{phase}.log"
    run = db.start_phase(video_id, phase, [], str(log_path))

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
            log_path.write_text(json.dumps(result.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
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
                if result.status != "completed":
                    raise RuntimeError(result.error_message)
        elif phase == "assemble":
            assemble_video(video, video_dir)
        elif phase == "package":
            db.update_video(video_id, status="completed")
            db.finish_phase(run["id"], "completed", 0, "")
            return True
        else:
            raise ValueError(f"Unknown phase: {phase}")
    except Exception as exc:
        if not log_path.exists():
            log_path.write_text(str(exc), encoding="utf-8")
        db.finish_phase(run["id"], "failed", 1, str(exc))
        return True

    following = next_phase(phase, video.get("content_type", "knowledge"))
    db.finish_phase(run["id"], "completed", 0, "")
    if following == "package" or following is None:
        db.update_video(video_id, current_phase="assemble", status="completed", error_message="")
    else:
        db.update_video(video_id, current_phase=following, status="queued", error_message="")
    return True


def process_next(db: Database, settings: Settings) -> bool:
    for video in db.list_videos():
        if video["status"] in {"queued", "running"}:
            return process_video_once(db, settings, video["id"])
    return False
