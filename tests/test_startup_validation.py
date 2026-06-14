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
    config_path = tmp_path / "pipeline.yaml"
    config_path.write_text(config_text, encoding="utf-8")
    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)
    validate_settings(settings)


def test_disabled_pipelines_require_no_pi_binary(tmp_path, monkeypatch):
    binary = _make_executable(tmp_path / "whisper-cli")
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    config = _minimal_config().format(binary=binary, model=model, cwd=tmp_path)
    config += "\npipelines:\n  enabled: false\n  pi:\n    binary: /no/such/pi\n"

    _load_and_validate(tmp_path, monkeypatch, config)


def test_enabled_pipelines_require_pi_binary(tmp_path, monkeypatch):
    binary = _make_executable(tmp_path / "whisper-cli")
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    config = _minimal_config().format(binary=binary, model=model, cwd=tmp_path)
    config += "\npipelines:\n  enabled: true\n  pi:\n    binary: /no/such/pi\n"

    with pytest.raises(StartupValidationError) as exc_info:
        _load_and_validate(tmp_path, monkeypatch, config)

    fields = [loc for loc, _ in exc_info.value.fields]
    assert "pipelines.pi.binary" in fields


def test_enabled_pipelines_accept_pi_command_from_path(tmp_path, monkeypatch):
    whisper = _make_executable(tmp_path / "whisper-cli")
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    _make_executable(tmp_path / "pi")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    config = _minimal_config().format(binary=whisper, model=model, cwd=tmp_path)
    config += "\npipelines:\n  enabled: true\n  pi:\n    binary: pi\n"

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


def test_auto_provider_fails_when_no_usable_provider(tmp_path, monkeypatch):
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

    fields = [loc for loc, _ in exc_info.value.fields]
    assert "asr.provider" in fields


def test_cms_credentials_allowed_when_no_cms_resource(tmp_path, monkeypatch):
    binary = _make_executable(tmp_path / "whisper-cli")
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    config = _minimal_config().format(binary=binary, model=model, cwd=tmp_path)
    config += "\ncms:\n  token: ''\n"

    _load_and_validate(tmp_path, monkeypatch, config)


def test_cms_credentials_required_when_cms_resource_enabled(tmp_path, monkeypatch):
    # Set these to empty strings so the real .env file cannot populate them.
    for env_key in (
        "BASECMS_TOKEN",
        "BASECMS_APP_ID",
        "BASECMS_NONCE",
        "BASECMS_SECRET",
        "BASECMS_TOKEN_URL",
    ):
        monkeypatch.setenv(env_key, "")
    binary = _make_executable(tmp_path / "whisper-cli")
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    config = _minimal_config().format(binary=binary, model=model, cwd=tmp_path)
    config += """
cms:
  token: ''
  token_gen:
    secret: ''
resource_providers:
  question_detail:
    provider: cms.question.detail
    path: /question/detail
"""

    with pytest.raises(StartupValidationError) as exc_info:
        _load_and_validate(tmp_path, monkeypatch, config)

    fields = [loc for loc, _ in exc_info.value.fields]
    assert "cms.token" in fields
    assert "cms.token_gen.secret" in fields


def test_cms_credentials_required_for_provider_keyed_defaults(tmp_path, monkeypatch):
    # Set these to empty strings so the real .env file cannot populate them.
    for env_key in (
        "BASECMS_TOKEN",
        "BASECMS_APP_ID",
        "BASECMS_NONCE",
        "BASECMS_SECRET",
        "BASECMS_TOKEN_URL",
    ):
        monkeypatch.setenv(env_key, "")
    binary = _make_executable(tmp_path / "whisper-cli")
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    config = _minimal_config().format(binary=binary, model=model, cwd=tmp_path)
    config += """
cms:
  token: ''
  token_gen:
    secret: ''
resource_providers:
  cms.question.detail:
    path: /question/detail
"""

    with pytest.raises(StartupValidationError) as exc_info:
        _load_and_validate(tmp_path, monkeypatch, config)

    fields = {loc for loc, _ in exc_info.value.fields}
    assert {"cms.token", "cms.token_gen.secret"} <= fields


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
pipelines:
  enabled: true
  pi:
    binary: /no/pi
"""

    with pytest.raises(StartupValidationError) as exc_info:
        _load_and_validate(tmp_path, monkeypatch, config)

    fields = {loc for loc, _ in exc_info.value.fields}
    assert "asr.whisper.binary" in fields
    assert "asr.whisper.model" in fields
    assert "openclaw.cwd" in fields
    assert "pipelines.pi.binary" in fields


def test_validation_diagnostics_do_not_leak_secret_values(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_HIVE_CMS_TOKEN", "super-secret-token")
    monkeypatch.setenv("VIDEO_HIVE_CMS_TOKEN_GEN_SECRET", "super-secret-gen")

    monkeypatch.setenv("VIDEO_HIVE_OPENCLAW_CWD", "/no/such/cwd")
    binary = _make_executable(tmp_path / "whisper-cli")
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    config = _minimal_config().format(binary=binary, model=model, cwd=tmp_path)
    config += """
cms:
  token: ''
  token_gen:
    secret: ''
resource_providers:
  question_detail:
    provider: cms.question.detail
    path: /question/detail
"""

    with pytest.raises(StartupValidationError) as exc_info:
        _load_and_validate(tmp_path, monkeypatch, config)

    message = str(exc_info.value)
    assert "super-secret-token" not in message
    assert "super-secret-gen" not in message
    # The env overrides make CMS credentials valid; the failing field is elsewhere.
    assert "openclaw.cwd" in message


def test_cms_resource_accepts_basecms_token_env(tmp_path, monkeypatch):
    monkeypatch.setenv("BASECMS_TOKEN", "basecms-token")
    binary = _make_executable(tmp_path / "whisper-cli")
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    config = _minimal_config().format(binary=binary, model=model, cwd=tmp_path)
    config += """
cms:
  token: ''
  token_gen:
    secret: ''
resource_providers:
  question_detail:
    provider: cms.question.detail
    path: /question/detail
"""

    _load_and_validate(tmp_path, monkeypatch, config)


def test_cms_resource_accepts_basecms_token_gen_env(tmp_path, monkeypatch):
    monkeypatch.setenv("BASECMS_APP_ID", "app-id")
    monkeypatch.setenv("BASECMS_NONCE", "nonce")
    monkeypatch.setenv("BASECMS_SECRET", "basecms-secret")
    monkeypatch.setenv("BASECMS_TOKEN_URL", "http://cms.example.com/token")
    binary = _make_executable(tmp_path / "whisper-cli")
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    config = _minimal_config().format(binary=binary, model=model, cwd=tmp_path)
    config += """
cms:
  token: ''
  token_gen:
    secret: ''
resource_providers:
  question_detail:
    provider: cms.question.detail
    path: /question/detail
"""

    _load_and_validate(tmp_path, monkeypatch, config)


def test_validate_runtime_can_be_called_directly(tmp_path, monkeypatch):
    binary = _make_executable(tmp_path / "whisper-cli")
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    config_path = tmp_path / "pipeline.yaml"
    config_path.write_text(
        _minimal_config().format(binary=binary, model=model, cwd=tmp_path),
        encoding="utf-8",
    )
    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    # Should not raise when all configured runtimes are usable.
    validate_runtime(settings.executor_runtime, settings.config)
