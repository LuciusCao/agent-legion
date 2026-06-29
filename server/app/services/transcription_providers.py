from pathlib import Path

from server.app.pipeline.transcribe_providers import (
    SenseVoiceProvider,
    TranscriptionProvider,
    WhisperCppProvider,
)
from server.app.settings import Settings


def _resolve_asr_path(settings: Settings, raw: str | None, default: str) -> Path:
    """Resolve an ASR config path: expand ``~`` and anchor relative paths to root_dir."""
    path = Path(raw or default).expanduser()
    if not path.is_absolute():
        path = settings.root_dir / path
    return path


def build_default_providers(settings: Settings) -> list[TranscriptionProvider]:
    asr = settings.config.get("asr", {})
    whisper = asr.get("whisper", {})
    sensevoice = asr.get("sensevoice", {})
    vad_model = whisper.get("vad_model")
    if vad_model and not Path(vad_model).expanduser().exists():
        raise FileNotFoundError(f"Configured VAD model not found: {vad_model}")
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
                    settings,
                    sensevoice.get("script"),
                    "server/app/pipeline/transcribe_sensevoice.py",
                )
            ),
            model_dir=str(
                _resolve_asr_path(
                    settings,
                    sensevoice.get("model_dir"),
                    "models/SenseVoiceSmall",
                )
            ),
            timeout=timeout,
        ),
    ]
    return providers
