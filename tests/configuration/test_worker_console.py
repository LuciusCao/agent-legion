"""Console URLs are validated before services start; errors never echo input."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from server.app.configuration.executor_runtime import (
    AgentWorkersRuntimeConfig,
    ExecutorRuntimeConfig,
)
from server.app.settings import load_settings

pytestmark = pytest.mark.no_db
CASES = json.loads((Path(__file__).parents[1] / "fixtures/worker-console-urls.json").read_text())


@pytest.mark.parametrize("value", CASES["valid"])
def test_valid_console_urls_are_preserved(value):
    assert AgentWorkersRuntimeConfig(console_url=value).console_url == value


@pytest.mark.parametrize("value", CASES["invalid"])
def test_invalid_console_urls_fail_typed_boundary(value):
    with pytest.raises(ValidationError, match="console_url"):
        AgentWorkersRuntimeConfig(console_url=value)


@pytest.mark.parametrize("value", [value for value in CASES["invalid"] if "\x00" not in value])
def test_invalid_console_urls_fail_settings_load(tmp_path, monkeypatch, value):
    monkeypatch.setenv("AGENT_LEGION_SKIP_DOTENV", "1")
    monkeypatch.setenv("AGENT_LEGION_WORKER_CONSOLE_URL", value)
    config = tmp_path / "config.yaml"
    config.write_text("{}")
    with pytest.raises(ValidationError, match="agent_workers.console_url"):
        load_settings(data_dir=tmp_path / "data", config_path=config)


@pytest.mark.parametrize("model", [AgentWorkersRuntimeConfig, ExecutorRuntimeConfig])
def test_validation_diagnostics_do_not_echo_url_credentials(model):
    value = "https://user:DO_NOT_LOG@host/?token=PRIVATE_QUERY"
    data = {"console_url": value}
    if model is ExecutorRuntimeConfig:
        data = {"agent_workers": data}
    with pytest.raises(ValidationError) as error:
        model.model_validate(data)
    for rendered in (str(error.value), repr(error.value)):
        assert "DO_NOT_LOG" not in rendered
        assert "PRIVATE_QUERY" not in rendered
        assert "HTTP(S)" in rendered


@pytest.mark.parametrize("explicit", ["", "https://configured.example"])
def test_explicit_setting_ignores_even_invalid_launcher_default(tmp_path, monkeypatch, explicit):
    monkeypatch.setenv("AGENT_LEGION_SKIP_DOTENV", "1")
    monkeypatch.setenv("AGENT_LEGION_WORKER_CONSOLE_URL", explicit)
    monkeypatch.setenv("AGENT_LEGION_WORKER_CONSOLE_DEFAULT_URL", "bad-default")
    config = tmp_path / "config.yaml"
    config.write_text("{}")
    result = load_settings(data_dir=tmp_path / "data", config_path=config)
    assert result.executor_runtime.agent_workers.console_url == explicit
