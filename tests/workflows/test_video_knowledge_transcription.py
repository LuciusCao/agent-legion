from pathlib import Path

import pytest

from server.app.pipeline.transcribe_providers import SenseVoiceProvider, WhisperCppProvider
from server.app.workflows.video_knowledge_transcription import build_default_providers


def test_build_default_providers_uses_config_defaults(settings) -> None:
    settings.config["asr"] = {}

    providers = build_default_providers(settings)

    whisper, sensevoice = providers
    assert isinstance(whisper, WhisperCppProvider)
    assert isinstance(sensevoice, SenseVoiceProvider)
    assert whisper.name == "whisper"
    assert sensevoice.name == "sensevoice"
    assert whisper.timeout == 900
    assert sensevoice.timeout == 900
    assert whisper.vad_model is None
    assert sensevoice.script == settings.root_dir / "server/app/pipeline/transcribe_sensevoice.py"
    assert sensevoice.model_dir == settings.root_dir / "models/SenseVoiceSmall"


def test_build_default_providers_honors_custom_config(settings, tmp_path: Path) -> None:
    vad_model = tmp_path / "ggml-silero.bin"
    vad_model.write_bytes(b"vad")
    settings.config["asr"] = {
        "timeout_seconds": "30",
        "whisper": {
            "binary": "/bin/echo",
            "model": "/tmp/ggml-custom.bin",
            "vad_model": str(vad_model),
        },
        "sensevoice": {
            "script": "relative/transcribe.py",
            "model_dir": "/opt/models/SenseVoiceCustom",
        },
    }

    whisper, sensevoice = build_default_providers(settings)

    assert whisper.timeout == 30
    assert sensevoice.timeout == 30
    assert whisper.binary == Path("/bin/echo")
    assert whisper.model == Path("/tmp/ggml-custom.bin")
    assert whisper.vad_model == vad_model
    assert sensevoice.script == settings.root_dir / "relative/transcribe.py"
    assert sensevoice.model_dir == Path("/opt/models/SenseVoiceCustom")


def test_build_default_providers_expands_user_in_asr_paths(settings) -> None:
    settings.config["asr"] = {
        "sensevoice": {"script": "~/bin/transcribe.py", "model_dir": "~/models/sensevoice"}
    }

    _, sensevoice = build_default_providers(settings)

    assert sensevoice.script == Path.home() / "bin/transcribe.py"
    assert sensevoice.model_dir == Path.home() / "models/sensevoice"


def test_build_default_providers_rejects_missing_vad_model(settings, tmp_path: Path) -> None:
    settings.config["asr"] = {"whisper": {"vad_model": str(tmp_path / "missing-vad.bin")}}

    with pytest.raises(FileNotFoundError, match="Configured VAD model not found"):
        build_default_providers(settings)
