from pathlib import Path

import pytest

from server.app.pipeline.transcribe_providers import SenseVoiceProvider, WhisperCppProvider
from server.app.workflows.video_knowledge_transcription import build_providers

pytestmark = pytest.mark.no_db

ROOT_DIR = Path("/repo")


def test_build_providers_uses_config_defaults() -> None:
    providers = build_providers({}, ROOT_DIR)

    whisper, sensevoice = providers
    assert isinstance(whisper, WhisperCppProvider)
    assert isinstance(sensevoice, SenseVoiceProvider)
    assert whisper.name == "whisper"
    assert sensevoice.name == "sensevoice"
    assert whisper.timeout == 900
    assert sensevoice.timeout == 900
    assert whisper.vad_model is None
    assert sensevoice.script == ROOT_DIR / "server/app/pipeline/transcribe_sensevoice.py"
    assert sensevoice.model_dir == ROOT_DIR / "models/SenseVoiceSmall"


def test_build_providers_honors_custom_config(tmp_path: Path) -> None:
    vad_model = tmp_path / "ggml-silero.bin"
    vad_model.write_bytes(b"vad")
    asr_config = {
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

    whisper, sensevoice = build_providers(asr_config, ROOT_DIR)

    assert whisper.timeout == 30
    assert sensevoice.timeout == 30
    assert whisper.binary == Path("/bin/echo")
    assert whisper.model == Path("/tmp/ggml-custom.bin")
    assert whisper.vad_model == vad_model
    assert sensevoice.script == ROOT_DIR / "relative/transcribe.py"
    assert sensevoice.model_dir == Path("/opt/models/SenseVoiceCustom")


def test_build_providers_expands_user_in_asr_paths() -> None:
    asr_config = {"sensevoice": {"script": "~/bin/transcribe.py", "model_dir": "~/models/sv"}}

    _, sensevoice = build_providers(asr_config, ROOT_DIR)

    assert sensevoice.script == Path.home() / "bin/transcribe.py"
    assert sensevoice.model_dir == Path.home() / "models/sv"


def test_build_providers_rejects_missing_vad_model(tmp_path: Path) -> None:
    asr_config = {"whisper": {"vad_model": str(tmp_path / "missing-vad.bin")}}

    with pytest.raises(FileNotFoundError, match="Configured VAD model not found"):
        build_providers(asr_config, ROOT_DIR)
