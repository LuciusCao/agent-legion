import json
import subprocess
from pathlib import Path

from server.app.agents import AgentStatusManager
from server.app.db import Database
from server.app.pipeline.assemble import assemble_video
from server.app.pipeline.download import download_video
from server.app.pipeline.fetch_url import fetch_knowledge_url, fetch_question_url, get_token
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


_runners: list[OpenClawRunner] = []
_busy_indices: set[int] = set()


def discover_openclaw_agents(timeout: int = 10) -> list[str]:
    try:
        result = subprocess.run(
            ["openclaw", "agents", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return []
        agents = json.loads(result.stdout)
        return [a["id"] for a in agents if isinstance(a, dict) and "id" in a]
    except Exception:
        return []


def _build_agent_command(base_template: list[str], agent_id: str) -> list[str]:
    result: list[str] = []
    i = 0
    while i < len(base_template):
        part = base_template[i]
        if part == "--agent" and i + 1 < len(base_template):
            result.extend(["--agent", agent_id])
            i += 2
        else:
            result.append(part)
            i += 1
    return result


def build_openclaw_runners(
    settings: Settings, discovered_agent_ids: list[str] | None = None
) -> list[OpenClawRunner]:
    openclaw = settings.config.get("openclaw", {})
    base_cwd = (settings.root_dir / str(openclaw.get("cwd", "."))).resolve()
    timeout_seconds = int(openclaw.get("timeout_seconds", 600))

    # 1. 显式配置的 runners（最高优先级）
    runners_config = openclaw.get("runners")
    if runners_config:
        return [
            OpenClawRunner(
                command_template=list(r["command_template"]),
                cwd=base_cwd,
                timeout_seconds=timeout_seconds,
            )
            for r in runners_config
        ]

    # 2. 基础命令模板
    base_template = list(
        openclaw.get(
            "command_template",
            [
                "openclaw",
                "agent",
                "--local",
                "--agent",
                "main",
                "--message",
                "{prompt_text}",
                "--json",
            ],
        )
    )

    # 3. 如果模板包含 --agent，动态发现可用 agents
    if "--agent" in base_template:
        agents = discovered_agent_ids if discovered_agent_ids is not None else discover_openclaw_agents()
        if agents:
            return [
                OpenClawRunner(
                    command_template=_build_agent_command(base_template, agent_id),
                    cwd=base_cwd,
                    timeout_seconds=timeout_seconds,
                )
                for agent_id in agents
            ]

    # 4. 回退：单个默认 runner
    return [
        OpenClawRunner(
            command_template=base_template,
            cwd=base_cwd,
            timeout_seconds=timeout_seconds,
        )
    ]


def build_openclaw_runner(settings: Settings) -> OpenClawRunner:
    return build_openclaw_runners(settings)[0]


def init_runners(settings: Settings, agent_manager: AgentStatusManager | None = None) -> int:
    global _runners, _busy_indices
    discovered_agent_ids = [agent.id for agent in agent_manager.agents] if agent_manager else None
    _runners = build_openclaw_runners(settings, discovered_agent_ids)
    _busy_indices = set()
    if agent_manager:
        for i, runner in enumerate(_runners):
            runner.agent_id = agent_manager.agents[i].id if i < len(agent_manager.agents) else f"runner-{i}"
    return len(_runners)


def acquire_runner() -> tuple[int, OpenClawRunner]:
    if not _runners:
        raise RuntimeError("Runners not initialized. Call init_runners() first.")
    for i, runner in enumerate(_runners):
        if i not in _busy_indices:
            _busy_indices.add(i)
            return i, runner
    raise RuntimeError("No free runner available")


def release_runner(index: int) -> None:
    _busy_indices.discard(index)


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
        try:
            if cms and video.get("external_id"):
                env = cms.get("env", "prod")
                token = get_token(env, cms)
                if video.get("content_type") == "knowledge":
                    api_url = cms.get("knowledge_url")
                    fetched_url = fetch_knowledge_url(video["external_id"], api_url, token) or ""
                else:
                    api_url = cms.get("question_url")
                    fetched_url = fetch_question_url(video["external_id"], api_url, token) or ""
        except Exception:
            fetched_url = ""
        if fetched_url:
            db.update_video(
                video_id,
                source_url=fetched_url,
                status="queued",
                current_phase="download",
            )
            video = db.get_video(video_id)
            phase = video["current_phase"]
        else:
            db.update_video(video_id, status="missing_url", current_phase="waiting_for_url")
            return False
    video_dir = Path(video["storage_dir"]) if video["storage_dir"] else settings.videos_dir / video_id
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


def process_next(
    db: Database,
    settings: Settings,
    openclaw_runner: OpenClawRunner | None = None,
) -> bool:
    for video in db.list_videos():
        if video["status"] in {"queued", "missing_url"} and process_video_once(
            db, settings, video["id"], openclaw_runner=openclaw_runner
        ):
            return True
    return False
