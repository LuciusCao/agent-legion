import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from server.app.cms.client import get_token
from server.app.cms.knowledge import lookup_knowledge_video
from server.app.cms.question import lookup_question_video
from server.app.db import Database
from server.app.pipeline.assemble import assemble_video
from server.app.pipeline.common import resolve_video_dir
from server.app.pipeline.download import download_video
from server.app.pipeline.executor import PhaseContext, PhaseExecutorRegistry
from server.app.pipeline.openclaw import OpenClawRunner
from server.app.pipeline.phases import AGENT_PHASES, next_phase
from server.app.pipeline.runners import build_openclaw_runner
from server.app.pipeline.transcribe import (
    SenseVoiceProvider,
    TranscriptionProvider,
    WhisperCppProvider,
    run_transcription_with_providers,
)
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


def validate_phase_outputs(video_dir: Path, phase_key: str) -> None:
    """Validate agent phase output format. Raise ValueError on invalid data."""

    def _load_json(path: Path) -> Any:
        if not path.exists():
            raise ValueError(f"Missing required file: {path.name}")
        return json.loads(path.read_text(encoding="utf-8"))

    if phase_key == "subtitle_review":
        report = _load_json(video_dir / "subtitle_review_report.json")
        if not isinstance(report, dict):
            raise ValueError("subtitle_review_report.json must be a JSON object")
        srt_path = video_dir / "subtitles_reviewed.srt"
        if not srt_path.exists():
            raise ValueError("subtitles_reviewed.srt is missing after subtitle_review")

    elif phase_key == "chapter_generate":
        data = _load_json(video_dir / "chapters.json")
        chapters = data.get("chapters", []) if isinstance(data, dict) else data
        if not isinstance(chapters, list):
            raise ValueError("chapters.json must contain a list of chapters")
        if not chapters:
            raise ValueError("chapters.json must contain at least one chapter")
        for idx, ch in enumerate(chapters):
            if not isinstance(ch, dict):
                raise ValueError(f"Chapter {idx + 1} must be an object")
            if "end_time" not in ch and "end" not in ch:
                raise ValueError(
                    f"Chapter {idx + 1} ('{ch.get('title', '')}') is missing 'end_time'. "
                    f"The chapter_generate agent must output 'end_time' for every chapter."
                )
            if not ch.get("title"):
                raise ValueError(f"Chapter {idx + 1} is missing 'title'")

    elif phase_key == "interaction_generate":
        data = _load_json(video_dir / "interactions.json")
        interactions = data.get("interactions", []) if isinstance(data, dict) else data
        if not isinstance(interactions, list):
            raise ValueError("interactions.json must contain an 'interactions' array")
        for idx, inter in enumerate(interactions):
            if not isinstance(inter, dict):
                raise ValueError(f"Interaction {idx + 1} must be an object")
            if not inter.get("id"):
                raise ValueError(f"Interaction {idx + 1} is missing 'id'")
            itype = inter.get("type", "")
            if itype not in {"example_practice", "video_summary", "interaction_summary"}:
                raise ValueError(
                    f"Interaction {idx + 1} has unknown type '{itype}'. "
                    f"Expected one of: example_practice, video_summary, interaction_summary"
                )
            if "trigger_time" not in inter:
                raise ValueError(f"Interaction {idx + 1} ('{inter.get('id')}') is missing 'trigger_time'")
            if not inter.get("instruction"):
                raise ValueError(f"Interaction {idx + 1} ('{inter.get('id')}') is missing 'instruction'")

    elif phase_key == "content_review":
        checklist = _load_json(video_dir / "checklist.json")
        if not isinstance(checklist, dict):
            raise ValueError("checklist.json must be a JSON object")
        review = _load_json(video_dir / "review_result.json")
        if not isinstance(review, dict):
            raise ValueError("review_result.json must be a JSON object")
        if "reviews" not in review:
            raise ValueError("review_result.json is missing 'reviews' array")


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


def _handle_download(ctx: PhaseContext) -> None:
    download_video(ctx.video["source_url"], ctx.video_dir / f"{ctx.video['id']}.mp4")


def _handle_transcribe(ctx: PhaseContext) -> None:
    active_providers = ctx.providers or build_default_providers(ctx.settings)
    result = run_transcription_with_providers(
        video_path=ctx.video_dir / f"{ctx.video['id']}.mp4",
        output_dir=ctx.video_dir,
        title=ctx.video["title"],
        duration=float(ctx.video.get("duration") or 0),
        mode=str(ctx.settings.config.get("asr", {}).get("provider", "auto")),
        providers=active_providers,
    )
    ctx.log_path.write_text(
        json.dumps(result.__dict__, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ctx.db.record_transcription_run(
        video_id=ctx.video["id"],
        provider=result.provider,
        status="completed",
        srt_entry_count=result.srt_entry_count,
        validation_summary=result.validation_summary,
        fallback_reason=result.fallback_reason,
    )


def _handle_agent_phase(ctx: PhaseContext) -> None:
    phase = ctx.video["current_phase"]
    agent_phase = AGENT_PHASES[phase]
    if not phase_outputs_sufficient(ctx.video_dir, phase, agent_phase.expected_outputs):
        runner = ctx.openclaw_runner or build_openclaw_runner(ctx.settings)
        result = runner.run(
            phase=agent_phase,
            video_id=ctx.video["id"],
            video_dir=ctx.video_dir,
            prompt_dir=ctx.settings.data_dir / "prompts",
            log_path=ctx.log_path,
        )
        if getattr(result, "command", None):
            ctx.db.update_phase_command(ctx.run["id"], result.command)
        if result.status != "completed":
            raise RuntimeError(result.error_message)
        validate_phase_outputs(ctx.video_dir, phase)


def _handle_assemble(ctx: PhaseContext) -> None:
    assemble_video(ctx.video, ctx.video_dir)


_default_registry = PhaseExecutorRegistry()
_default_registry.register("download", _handle_download)
_default_registry.register("transcribe", _handle_transcribe)
_default_registry.register("subtitle_review", _handle_agent_phase)
_default_registry.register("chapter_generate", _handle_agent_phase)
_default_registry.register("interaction_generate", _handle_agent_phase)
_default_registry.register("content_review", _handle_agent_phase)
_default_registry.register("assemble", _handle_assemble)


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
        ctx = PhaseContext(
            video=video,
            video_dir=video_dir,
            settings=settings,
            db=db,
            log_path=log_path,
            providers=providers,
            openclaw_runner=openclaw_runner,
            run=run,
        )
        _default_registry.execute(phase, ctx)
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
