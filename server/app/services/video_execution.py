import json
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
from server.app.pipeline.transcribe import TranscriptionProvider, run_transcription_with_providers
from server.app.pipeline.validators import phase_outputs_sufficient, validate_phase_outputs
from server.app.services.interaction_cache import InteractionCacheService
from server.app.services.transcription_providers import build_default_providers
from server.app.settings import Settings
from server.app.storage_paths import make_data_relative


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
    if phase == "content_review" and ctx.video.get("content_type") == "knowledge":
        InteractionCacheService(ctx.db, ctx.settings).refresh(ctx.video["id"])


def _handle_assemble(ctx: PhaseContext) -> None:
    assemble_video(ctx.video, ctx.video_dir)
    if ctx.video.get("content_type") == "knowledge":
        InteractionCacheService(ctx.db, ctx.settings).refresh(ctx.video["id"])


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
    stop_after_phase: str | None = None,
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
        update_fields: dict[str, Any]
        if fetched_url:
            update_fields = {
                "source_url": fetched_url,
                "status": "queued",
                "current_phase": "download",
                "error_message": "",
            }
            if fetched_source_uuid:
                update_fields["source_uuid"] = fetched_source_uuid
            db.update_video(video_id, **update_fields)
            video = db.get_video(video_id)
            if video is None:
                return False
            phase = video["current_phase"]
        else:
            if not fetch_error and cms and video.get("external_id"):
                fetch_error = "fetch url failed: CMS did not return a video URL"
            update_fields = {
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
    run = db.start_phase(video_id, phase, [], make_data_relative(log_path, settings.data_dir))
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
    elif stop_after_phase == phase:
        db.update_video(video_id, current_phase=following, status="queued", error_message="")
    else:
        db.update_video(video_id, current_phase=following, status="queued", error_message="")

    if phase == "assemble" and settings.config.get("cleanup_video_after_assemble", False):
        mp4_path = video_dir / f"{video_id}.mp4"
        if mp4_path.exists():
            try:
                mp4_path.unlink()
            except OSError as exc:
                if log_path.exists():
                    with log_path.open("a", encoding="utf-8") as f:
                        f.write(f"\nCleanup warning: {exc}")

    return True
