"""video_knowledge node: transcribe the downloaded source video.

Runs ASR over ``source.mp4`` with the configured provider chain
(``asr.provider`` from settings, overridable via the runtime settings
snapshot) and writes ``transcription.json``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from server.app.pipeline.transcribe import run_transcription_with_providers
from server.app.settings import load_settings
from server.app.video_capabilities.contracts import VideoKnowledgeInput
from server.app.workflows.video_knowledge_transcription import build_default_providers

logger = logging.getLogger(__name__)


def _load_video_input(job_dir: Path) -> VideoKnowledgeInput:
    raw = json.loads((job_dir / "video_input.json").read_text(encoding="utf-8"))
    return VideoKnowledgeInput.from_mapping(raw)


def run(
    job: dict[str, Any],
    job_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> None:
    settings = load_settings()
    if runtime and runtime.get("settings_config"):
        settings.config.update(runtime["settings_config"])
    video_input = _load_video_input(job_dir)
    mode = str(settings.config.get("asr", {}).get("provider", "auto"))
    result = run_transcription_with_providers(
        video_path=job_dir / "source.mp4",
        output_dir=job_dir,
        title=video_input.title,
        duration=0,
        mode=mode,
        providers=build_default_providers(settings),
    )
    (job_dir / "transcription.json").write_text(
        json.dumps(result.__dict__, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
