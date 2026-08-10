"""Unit tests for workflow_nodes/video_transcribe.py (transcribe_video node)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from workflow_nodes import video_transcribe

pytestmark = pytest.mark.no_db


def test_asr_config_merges_settings_and_node_config() -> None:
    runtime = {
        "settings_config": {
            "asr": {
                "provider": "auto",
                "timeout_seconds": 900,
                "whisper": {"binary": "/env/whisper-cli"},
            }
        },
        "node_config": {"provider": "whisper", "timeout_seconds": 120},
    }

    merged = video_transcribe._asr_config(runtime)

    # node_config 业务参数覆盖 settings 级（env 注入）值。
    assert merged["provider"] == "whisper"
    assert merged["timeout_seconds"] == 120
    # env 注入的机器路径保留。
    assert merged["whisper"] == {"binary": "/env/whisper-cli"}


def test_asr_config_ignores_empty_node_config_values() -> None:
    runtime = {
        "settings_config": {"asr": {"provider": "sensevoice"}},
        "node_config": {"provider": "", "timeout_seconds": None},
    }

    merged = video_transcribe._asr_config(runtime)

    assert merged["provider"] == "sensevoice"
    assert "timeout_seconds" not in merged


def test_asr_config_without_runtime_is_empty() -> None:
    assert video_transcribe._asr_config(None) == {}
    assert video_transcribe._asr_config({}) == {}


def _write_video_input(job_dir: Path) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "video_input.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entity_type": "video",
                "content_type": "knowledge",
                "external_id": "v-1",
                "title": "demo",
            }
        ),
        encoding="utf-8",
    )


def test_run_is_driven_by_node_config(monkeypatch, tmp_path: Path) -> None:
    """run() resolves ASR from the runtime snapshot, never from load_settings."""
    _write_video_input(tmp_path)
    captured: dict[str, Any] = {}

    def _fake_transcribe(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(video_transcribe, "run_transcription_with_providers", _fake_transcribe)
    monkeypatch.setattr(
        video_transcribe, "build_providers", lambda asr_config, root_dir: ["providers"]
    )
    monkeypatch.setattr(video_transcribe, "get_video_duration", lambda video_path: 100.0)
    # 节点不再读全量 settings：load_settings 一旦被调即失败。
    monkeypatch.setattr(
        "server.app.settings.load_settings",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("load_settings called")),
    )

    runtime = {
        "settings_config": {"asr": {"whisper": {"binary": "/env/whisper-cli"}}},
        "node_config": {"provider": "whisper"},
    }
    video_transcribe.run({}, tmp_path, runtime)

    assert captured["mode"] == "whisper"
    assert captured["providers"] == ["providers"]
    assert captured["duration"] == 100.0  # 真实时长驱动覆盖率校验，不再是 0
    assert (tmp_path / "transcription.json").is_file()


def test_run_defaults_to_auto_mode(monkeypatch, tmp_path: Path) -> None:
    _write_video_input(tmp_path)
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        video_transcribe,
        "run_transcription_with_providers",
        lambda **kwargs: (captured.update(kwargs), SimpleNamespace())[1],
    )
    monkeypatch.setattr(
        video_transcribe, "build_providers", lambda asr_config, root_dir: ["providers"]
    )
    monkeypatch.setattr(video_transcribe, "get_video_duration", lambda video_path: 60.0)

    video_transcribe.run({}, tmp_path, {})

    assert captured["mode"] == "auto"


def test_run_fails_loudly_when_duration_unprobeable(monkeypatch, tmp_path: Path) -> None:
    """ffprobe 失败（返回 0）时必须显式报错，不允许静默传 0 使覆盖率校验失效。"""
    _write_video_input(tmp_path)
    monkeypatch.setattr(video_transcribe, "get_video_duration", lambda video_path: 0.0)
    called = False

    def _fake_transcribe(**kwargs: Any) -> Any:
        nonlocal called
        called = True
        return SimpleNamespace()

    monkeypatch.setattr(video_transcribe, "run_transcription_with_providers", _fake_transcribe)

    with pytest.raises(RuntimeError, match="duration"):
        video_transcribe.run({}, tmp_path, {})

    assert called is False
