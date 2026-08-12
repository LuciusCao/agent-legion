"""video_knowledge node: transcribe the downloaded source video.

Runs ASR over ``source.mp4`` with the configured provider chain and writes
``transcription.json``. The yaml ``asr:`` section is retired: the effective
config merges the env-injected machine paths (``AGENT_LEGION_ASR_*``, arriving
via the runtime settings snapshot) with the node/workspace business
parameters (``provider`` / ``timeout_seconds`` from the capability
config_schema, arriving via ``node_config``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.pipeline.assemble import get_video_duration
from server.app.pipeline.transcribe import run_transcription_with_providers
from server.app.video_capabilities.contracts import VideoKnowledgeInput
from server.app.workflows.video_knowledge_transcription import build_providers
from workspace_libs.node_sdk import NodeContext

# Repo root anchors relative ASR paths (e.g. the bundled SenseVoice script);
# same derivation as server.app.settings.PROJECT_ROOT.
_ROOT_DIR = Path(__file__).resolve().parents[1]


def run(
    job: dict[str, Any],
    job_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> None:
    ctx = NodeContext(job, job_dir, runtime)
    # Settings-level ``asr`` (env-injected machine paths) as the base,
    # node/workspace business overrides (config_schema) win — see
    # NodeContext.service_config.
    asr_config = ctx.service_config(section="asr")
    video_input = VideoKnowledgeInput.from_mapping(ctx.artifacts.read_json("video_input.json"))
    mode = str(asr_config.get("provider", "auto"))
    video_path = job_dir / "source.mp4"
    ctx.checkpoint()
    # 覆盖率校验需要真实时长：ffprobe 失败（返回 0）时显式报错，而不是静默
    # 传 0 让 validate_srt 的 coverage 检查永久失效。ffmpeg/ffprobe 本就是
    # ASR provider 链（whisper/sensevoice）的运行时依赖。
    duration = get_video_duration(video_path)
    if duration <= 0:
        raise RuntimeError(f"cannot probe video duration for coverage validation: {video_path}")
    result = run_transcription_with_providers(
        video_path=video_path,
        output_dir=job_dir,
        title=video_input.title,
        duration=duration,
        mode=mode,
        providers=build_providers(asr_config, _ROOT_DIR),
    )
    ctx.artifacts.write_json("transcription.json", result.__dict__)
