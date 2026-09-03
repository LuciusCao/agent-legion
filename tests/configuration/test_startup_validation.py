"""Startup validation for runtime dependencies and environment overrides."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from server.app.configuration.executor_runtime import validate_runtime
from server.app.settings import load_settings, validate_settings


def _make_executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _minimal_config() -> str:
    return "\ndata_dir: data\n"


def _load_and_validate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_text: str):
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(config_text, encoding="utf-8")
    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)
    validate_settings(settings)


def test_agent_workflows_do_not_require_pi_binary_on_host(tmp_path, monkeypatch):
    # ``workflows.enabled`` is retired (#385/#389): agent runtimes are
    # preflighted on the Worker side, so the host never needs a pi binary.
    _load_and_validate(tmp_path, monkeypatch, _minimal_config())


def test_enabled_workflows_accept_pi_command_from_path(tmp_path, monkeypatch):
    """kind:pi 本地 executor（死路径保留）要求 pi 二进制在 PATH 上。

    workflows.pi yaml 块已退役（agent 配置治理 phase 3；PiRuntimeConfig
    已随死代码清理删除，pi 二进制由 agent_runtime catalog 的 adapter 钉死）。
    """
    _make_executable(tmp_path / "pi")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    config = _minimal_config()
    config += (
        "executors:\n"
        "  pi-legacy:\n"
        "    kind: pi\n"
        "    global_capacity: 1\n"
        "    capabilities:\n"
        "      legacy_skill:\n"
        "        skill: question/legacy\n"
    )

    _load_and_validate(tmp_path, monkeypatch, config)


def test_validate_runtime_can_be_called_directly(tmp_path, monkeypatch):
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(_minimal_config(), encoding="utf-8")
    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    # Should not raise when all configured runtimes are usable.
    validate_runtime(settings.executor_runtime, settings.config)
