"""video_knowledge node: transcribe the downloaded source video.

Runs ASR over ``source.mp4`` with the configured provider chain and writes
``transcription.json``. The yaml ``asr:`` section is retired: the effective
config merges the env-injected machine paths (``AGENT_LEGION_ASR_*``, arriving
via the runtime settings snapshot) with the node/workspace business
parameters (``provider`` / ``timeout_seconds`` from the capability
config_schema, arriving via ``node_config``).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from server.app.pipeline.transcribe import run_transcription_with_providers
from server.app.video_capabilities.contracts import VideoKnowledgeInput
from server.app.workflows.video_knowledge_transcription import build_providers

logger = logging.getLogger(__name__)

# Repo root anchors relative ASR paths (e.g. the bundled SenseVoice script);
# same derivation as server.app.settings.PROJECT_ROOT.
_ROOT_DIR = Path(__file__).resolve().parents[1]


def _load_video_input(job_dir: Path) -> VideoKnowledgeInput:
    raw = json.loads((job_dir / "video_input.json").read_text(encoding="utf-8"))
    return VideoKnowledgeInput.from_mapping(raw)


def _asr_config(runtime: dict[str, Any] | None) -> dict[str, Any]:
    """Effective ASR config: settings-level ``asr`` (env-injected) + node config.

    Node config carries the config_schema defaults (factory values) plus any
    node/workspace overrides and wins over the settings-level keys (same merge
    precedence as question_intake's ``_cms_config``).
    """
    runtime = runtime or {}
    merged: dict[str, Any] = {}
    settings_config = runtime.get("settings_config")
    if isinstance(settings_config, dict):
        asr = settings_config.get("asr")
        if isinstance(asr, dict):
            merged = dict(asr)
    node_config = runtime.get("node_config")
    if isinstance(node_config, dict):
        merged.update({key: value for key, value in node_config.items() if value not in (None, "")})
    return merged


def run(
    job: dict[str, Any],
    job_dir: Path,
    runtime: dict[str, Any] | None = None,
) -> None:
    asr_config = _asr_config(runtime)
    video_input = _load_video_input(job_dir)
    mode = str(asr_config.get("provider", "auto"))
    result = run_transcription_with_providers(
        video_path=job_dir / "source.mp4",
        output_dir=job_dir,
        title=video_input.title,
        duration=0,
        mode=mode,
        providers=build_providers(asr_config, _ROOT_DIR),
    )
    (job_dir / "transcription.json").write_text(
        json.dumps(result.__dict__, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
