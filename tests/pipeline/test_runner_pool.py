from __future__ import annotations

import json
import subprocess

import pytest

from server.app.pipeline.runners import RunnerPool, discover_openclaw_agents
from server.app.services.transcription_providers import build_default_providers
from server.app.settings import load_settings


def test_discover_openclaw_agents_uses_cli_json(monkeypatch):
    def fake_run(command, capture_output, text, timeout):
        assert command == ["openclaw", "agents", "list", "--json"]
        assert capture_output is True
        assert text is True
        assert timeout == 10
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps([{"id": "main"}, {"id": "agent_1"}]),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert discover_openclaw_agents() == ["main", "agent_1"]


def test_runner_pool_from_settings_returns_explicit_runner_count(tmp_path):
    settings = load_settings(data_dir=tmp_path)
    settings.config["openclaw"]["runners"] = [
        {"command_template": ["openclaw", "agent", "--agent", "main"]},
        {"command_template": ["openclaw", "agent", "--agent", "agent_1"]},
    ]

    pool = RunnerPool.from_settings(settings)
    assert pool.size() == 2


def test_build_default_providers_with_missing_vad_model(tmp_path, settings):
    settings.config["asr"] = {
        "whisper": {"binary": "whisper", "model": "model.bin", "vad_model": "/nonexistent/vad.bin"},
        "sensevoice": {},
    }
    with pytest.raises(FileNotFoundError, match="VAD model not found"):
        build_default_providers(settings)


def test_build_default_providers_without_vad_model(tmp_path, settings):
    settings.config["asr"] = {
        "whisper": {"binary": "whisper", "model": "model.bin"},
        "sensevoice": {},
    }
    providers = build_default_providers(settings)
    assert len(providers) == 2
    assert providers[0].vad_model is None


def test_build_default_providers_resolves_sensevoice_tilde_paths(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    home = tmp_path / "fake_home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    model_dir = home / ".cache" / "SenseVoiceSmall"
    model_dir.mkdir(parents=True)
    script = tmp_path / "server" / "app" / "pipeline" / "transcribe_sensevoice.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# fake script\n", encoding="utf-8")

    settings = MagicMock()
    settings.root_dir = tmp_path
    settings.config = {
        "asr": {
            "whisper": {"binary": "whisper", "model": "model.bin"},
            "sensevoice": {
                "script": "server/app/pipeline/transcribe_sensevoice.py",
                "model_dir": "~/.cache/SenseVoiceSmall",
            },
        }
    }
    providers = build_default_providers(settings)
    assert providers[1].script == script
    assert providers[1].model_dir == model_dir


def test_build_default_providers_uses_actual_sensevoice_script(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    home = tmp_path / "fake_home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    (home / ".cache" / "SenseVoiceSmall").mkdir(parents=True)
    script = tmp_path / "server" / "app" / "pipeline" / "transcribe_sensevoice.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# fake script\n", encoding="utf-8")

    settings = MagicMock()
    settings.root_dir = tmp_path
    settings.config = {
        "asr": {
            "whisper": {"binary": "whisper", "model": "model.bin"},
            "sensevoice": {},
        }
    }
    providers = build_default_providers(settings)
    assert providers[1].script == script
