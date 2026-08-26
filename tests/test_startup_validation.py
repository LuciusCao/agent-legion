"""Startup validation for runtime dependencies and environment overrides."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from server.app.executors.runtime_config import StartupValidationError, validate_runtime
from server.app.settings import load_settings, validate_settings


def _make_executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _minimal_config() -> str:
    return """
data_dir: data
openclaw:
  cwd: {cwd}
"""


def _load_and_validate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_text: str):
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(config_text, encoding="utf-8")
    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)
    validate_settings(settings)


def test_disabled_workflows_require_no_pi_binary(tmp_path, monkeypatch):
    config = _minimal_config().format(cwd=tmp_path)
    config += "\nworkflows:\n  enabled: false\n"

    _load_and_validate(tmp_path, monkeypatch, config)


def test_agent_workflows_do_not_require_pi_binary_on_host(tmp_path, monkeypatch):
    config = _minimal_config().format(cwd=tmp_path)
    config += "\nworkflows:\n  enabled: true\n"

    _load_and_validate(tmp_path, monkeypatch, config)


def test_enabled_workflows_accept_pi_command_from_path(tmp_path, monkeypatch):
    """kind:pi 本地 executor（死路径保留）要求 pi 二进制在 PATH 上。

    workflows.pi yaml 块已退役（agent 配置治理 phase 3），PiRuntimeConfig
    只剩硬编码默认 binary="pi"。
    """
    _make_executable(tmp_path / "pi")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    config = _minimal_config().format(cwd=tmp_path)
    config += (
        "\nworkflows:\n  enabled: true\n"
        "executors:\n"
        "  pi-legacy:\n"
        "    kind: pi\n"
        "    global_capacity: 1\n"
        "    capabilities:\n"
        "      legacy_skill:\n"
        "        skill: question/legacy\n"
    )

    _load_and_validate(tmp_path, monkeypatch, config)


def test_openclaw_cwd_must_exist(tmp_path, monkeypatch):
    config = _minimal_config().format(cwd=tmp_path / "missing")

    with pytest.raises(StartupValidationError) as exc_info:
        _load_and_validate(tmp_path, monkeypatch, config)

    fields = [loc for loc, _ in exc_info.value.fields]
    assert "openclaw.cwd" in fields


def test_validation_diagnostics_do_not_leak_secret_values(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_LEGION_CMS_TOKEN", "super-secret-token")
    monkeypatch.setenv("AGENT_LEGION_CMS_TOKEN_GEN_SECRET", "super-secret-gen")

    monkeypatch.setenv("AGENT_LEGION_OPENCLAW_CWD", "/no/such/cwd")
    config = _minimal_config().format(cwd=tmp_path)

    with pytest.raises(StartupValidationError) as exc_info:
        _load_and_validate(tmp_path, monkeypatch, config)

    message = str(exc_info.value)
    assert "super-secret-token" not in message
    assert "super-secret-gen" not in message
    # The failing field is unrelated to the CMS env values above.
    assert "openclaw.cwd" in message


def test_validate_runtime_can_be_called_directly(tmp_path, monkeypatch):
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(
        _minimal_config().format(cwd=tmp_path),
        encoding="utf-8",
    )
    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    # Should not raise when all configured runtimes are usable.
    validate_runtime(settings.executor_runtime, settings.config)
