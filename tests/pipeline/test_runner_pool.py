from __future__ import annotations

import json
import subprocess

import pytest

from server.app.pipeline.runners import RunnerPool, discover_openclaw_agents
from server.app.settings import load_settings
from server.app.worker import (
    build_default_providers,
    get_phase_concurrency_limit,
)


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


def test_transcribe_concurrency_limit_is_configurable(settings):
    settings.config["worker"] = {"phase_concurrency": {"transcribe": 3}}

    assert get_phase_concurrency_limit(settings, "download") == 10
    assert get_phase_concurrency_limit(settings, "transcribe") == 3


def test_worker_control_tick():
    from server.app.worker_control import WorkerControl

    wc = WorkerControl()
    assert not wc.consume_tick()
    wc.request_tick()
    assert wc.consume_tick()
    assert not wc.consume_tick()


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
