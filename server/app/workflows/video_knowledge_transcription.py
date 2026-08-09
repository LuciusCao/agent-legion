from pathlib import Path
from typing import Any

from server.app.pipeline.transcribe_providers import (
    SenseVoiceProvider,
    TranscriptionProvider,
    WhisperCppProvider,
)


def _resolve_asr_path(root_dir: Path, raw: str | None, default: str) -> Path:
    """Resolve an ASR config path: expand ``~`` and anchor relative paths to root_dir."""
    path = Path(raw or default).expanduser()
    if not path.is_absolute():
        path = root_dir / path
    return path


def build_providers(asr_config: dict[str, Any], root_dir: Path) -> list[TranscriptionProvider]:
    """Build the ASR provider chain from the effective ``asr`` config mapping.

    The mapping merges env-injected machine paths (``AGENT_LEGION_ASR_*`` via
    ``settings_config``) with the node/workspace business parameters
    (``provider`` / ``timeout_seconds`` from the capability config_schema);
    the yaml ``asr:`` section is retired.
    """
    asr = asr_config
    whisper = asr.get("whisper", {})
    sensevoice = asr.get("sensevoice", {})
    vad_model = whisper.get("vad_model")
    if vad_model and not Path(vad_model).expanduser().exists():
        raise FileNotFoundError(
            f"Configured VAD model not found: {vad_model} "
            "(set env AGENT_LEGION_ASR_WHISPER_VAD_MODEL or clear the value)"
        )
    timeout = int(asr.get("timeout_seconds", 900))
    providers: list[TranscriptionProvider] = [
        WhisperCppProvider(
            binary=str(whisper.get("binary", "")),
            model=str(whisper.get("model", "")),
            vad_model=vad_model,
            timeout=timeout,
        ),
        SenseVoiceProvider(
            script=str(
                _resolve_asr_path(
                    root_dir,
                    sensevoice.get("script"),
                    "server/app/pipeline/transcribe_sensevoice.py",
                )
            ),
            model_dir=str(
                _resolve_asr_path(
                    root_dir,
                    sensevoice.get("model_dir"),
                    "models/SenseVoiceSmall",
                )
            ),
            timeout=timeout,
        ),
    ]
    return providers
