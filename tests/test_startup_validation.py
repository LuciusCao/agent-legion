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
asr:
  provider: whisper
  whisper:
    binary: {binary}
    model: {model}
openclaw:
  cwd: {cwd}
  command_template:
    - openclaw
    - agent
"""


def _load_and_validate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_text: str):
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(config_text, encoding="utf-8")
    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)
    validate_settings(settings)


def test_disabled_workflows_require_no_pi_binary(tmp_path, monkeypatch):
    binary = _make_executable(tmp_path / "whisper-cli")
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    config = _minimal_config().format(binary=binary, model=model, cwd=tmp_path)
    config += "\nworkflows:\n  enabled: false\n"

    _load_and_validate(tmp_path, monkeypatch, config)


def test_agent_workflows_do_not_require_pi_binary_on_host(tmp_path, monkeypatch):
    binary = _make_executable(tmp_path / "whisper-cli")
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    config = _minimal_config().format(binary=binary, model=model, cwd=tmp_path)
    config += "\nworkflows:\n  enabled: true\n"

    _load_and_validate(tmp_path, monkeypatch, config)


def test_enabled_workflows_accept_pi_command_from_path(tmp_path, monkeypatch):
    """kind:pi 本地 executor（死路径保留）要求 pi 二进制在 PATH 上。

    workflows.pi yaml 块已退役（agent 配置治理 phase 3），PiRuntimeConfig
    只剩硬编码默认 binary="pi"。
    """
    whisper = _make_executable(tmp_path / "whisper-cli")
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    _make_executable(tmp_path / "pi")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    config = _minimal_config().format(binary=whisper, model=model, cwd=tmp_path)
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


def test_whisper_provider_accepts_binary_from_path(tmp_path, monkeypatch):
    _make_executable(tmp_path / "whisper-cli")
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    config = _minimal_config().format(binary="whisper-cli", model=model, cwd=tmp_path)

    _load_and_validate(tmp_path, monkeypatch, config)


def test_openclaw_cwd_must_exist(tmp_path, monkeypatch):
    binary = _make_executable(tmp_path / "whisper-cli")
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    config = _minimal_config().format(binary=binary, model=model, cwd=tmp_path / "missing")

    with pytest.raises(StartupValidationError) as exc_info:
        _load_and_validate(tmp_path, monkeypatch, config)

    fields = [loc for loc, _ in exc_info.value.fields]
    assert "openclaw.cwd" in fields


def test_whisper_provider_requires_executable_binary_and_model(tmp_path, monkeypatch):
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    config = _minimal_config().format(
        binary=tmp_path / "missing-whisper", model=model, cwd=tmp_path
    )

    with pytest.raises(StartupValidationError) as exc_info:
        _load_and_validate(tmp_path, monkeypatch, config)

    fields = [loc for loc, _ in exc_info.value.fields]
    assert "asr.whisper.binary" in fields


def test_whisper_provider_requires_model_file(tmp_path, monkeypatch):
    binary = _make_executable(tmp_path / "whisper-cli")
    config = _minimal_config().format(binary=binary, model=tmp_path / "missing.bin", cwd=tmp_path)

    with pytest.raises(StartupValidationError) as exc_info:
        _load_and_validate(tmp_path, monkeypatch, config)

    fields = [loc for loc, _ in exc_info.value.fields]
    assert "asr.whisper.model" in fields


def test_sensevoice_provider_requires_model_dir(tmp_path, monkeypatch):
    config = """
data_dir: data
asr:
  provider: sensevoice
  sensevoice:
    script: server/app/pipeline/transcribe_sensevoice.py
    model_dir: {model_dir}
openclaw:
  cwd: {cwd}
  command_template:
    - openclaw
    - agent
""".format(model_dir=tmp_path / "missing-model", cwd=tmp_path)

    with pytest.raises(StartupValidationError) as exc_info:
        _load_and_validate(tmp_path, monkeypatch, config)

    fields = [loc for loc, _ in exc_info.value.fields]
    assert "asr.sensevoice.model_dir" in fields


def test_auto_provider_passes_with_one_usable_provider(tmp_path, monkeypatch):
    binary = _make_executable(tmp_path / "whisper-cli")
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    config = f"""
data_dir: data
asr:
  provider: auto
  whisper:
    binary: {binary}
    model: {model}
openclaw:
  cwd: {tmp_path}
  command_template:
    - openclaw
    - agent
"""

    _load_and_validate(tmp_path, monkeypatch, config)


def test_auto_provider_no_longer_requires_a_usable_provider(tmp_path, monkeypatch):
    """The global 'auto needs one usable provider' check is retired.

    ASR business parameters (provider/timeout) live in the transcribe_video
    capability config_schema; startup only validates configured paths. With no
    asr paths configured at all the server starts, and a missing binary
    surfaces as the provider's FileNotFoundError at transcription time.
    """
    config = f"""
data_dir: data
asr:
  provider: auto
openclaw:
  cwd: {tmp_path}
  command_template:
    - openclaw
    - agent
"""

    _load_and_validate(tmp_path, monkeypatch, config)


def test_configured_asr_paths_still_fail_fast(tmp_path, monkeypatch):
    """Provided asr paths (env-injected or explicit config) must resolve."""
    config = f"""
data_dir: data
asr:
  provider: auto
  whisper:
    binary: /no/whisper
    model: /no/model
  sensevoice:
    script: /no/script
    model_dir: /no/model_dir
openclaw:
  cwd: {tmp_path}
  command_template:
    - openclaw
    - agent
"""

    with pytest.raises(StartupValidationError) as exc_info:
        _load_and_validate(tmp_path, monkeypatch, config)

    fields = {loc for loc, _ in exc_info.value.fields}
    assert "asr.provider" not in fields
    assert "asr.whisper.binary" in fields
    assert "asr.whisper.model" in fields
    assert "asr.sensevoice.script" in fields
    assert "asr.sensevoice.model_dir" in fields


def test_env_injected_asr_path_fails_fast(tmp_path, monkeypatch):
    """A typo'd AGENT_LEGION_ASR_* env value fails startup, not transcription."""
    monkeypatch.setenv("AGENT_LEGION_ASR_WHISPER_VAD_MODEL", "/no/vad.bin")
    config = f"""
data_dir: data
openclaw:
  cwd: {tmp_path}
  command_template:
    - openclaw
    - agent
"""

    with pytest.raises(StartupValidationError) as exc_info:
        _load_and_validate(tmp_path, monkeypatch, config)

    fields = [loc for loc, _ in exc_info.value.fields]
    assert "asr.whisper.vad_model" in fields


def test_missing_asr_config_starts_clean(tmp_path, monkeypatch):
    """No asr configuration anywhere: startup validates nothing ASR-related."""
    config = f"""
data_dir: data
openclaw:
  cwd: {tmp_path}
  command_template:
    - openclaw
    - agent
"""

    _load_and_validate(tmp_path, monkeypatch, config)


def test_aggregate_invalid_fields_in_one_exception(tmp_path, monkeypatch):
    config = """
data_dir: data
asr:
  provider: whisper
  whisper:
    binary: /no/whisper
    model: /no/model
openclaw:
  cwd: /no/cwd
  command_template:
    - openclaw
    - agent
workflows:
  enabled: true
"""

    with pytest.raises(StartupValidationError) as exc_info:
        _load_and_validate(tmp_path, monkeypatch, config)

    fields = {loc for loc, _ in exc_info.value.fields}
    assert "asr.whisper.binary" in fields
    assert "asr.whisper.model" in fields
    assert "openclaw.cwd" in fields


def test_validation_diagnostics_do_not_leak_secret_values(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_LEGION_CMS_TOKEN", "super-secret-token")
    monkeypatch.setenv("AGENT_LEGION_CMS_TOKEN_GEN_SECRET", "super-secret-gen")

    monkeypatch.setenv("AGENT_LEGION_OPENCLAW_CWD", "/no/such/cwd")
    binary = _make_executable(tmp_path / "whisper-cli")
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    config = _minimal_config().format(binary=binary, model=model, cwd=tmp_path)
    config += """
cms:
  base_url: http://cms.example.com
"""

    with pytest.raises(StartupValidationError) as exc_info:
        _load_and_validate(tmp_path, monkeypatch, config)

    message = str(exc_info.value)
    assert "super-secret-token" not in message
    assert "super-secret-gen" not in message
    # The failing field is unrelated to the CMS env values above.
    assert "openclaw.cwd" in message


def test_validate_runtime_can_be_called_directly(tmp_path, monkeypatch):
    binary = _make_executable(tmp_path / "whisper-cli")
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    config_path = tmp_path / "workflow.yaml"
    config_path.write_text(
        _minimal_config().format(binary=binary, model=model, cwd=tmp_path),
        encoding="utf-8",
    )
    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    # Should not raise when all configured runtimes are usable.
    validate_runtime(settings.executor_runtime, settings.config)
