from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from server.app.settings import load_settings
from server.app.studio_chat.timing import StudioTimingConfig

pytestmark = pytest.mark.no_db

ENV_FIELDS = {
    "AGENT_LEGION_STUDIO_CHAT_PROMPT_TIMEOUT_SECONDS": "prompt_timeout_seconds",
    "AGENT_LEGION_STUDIO_CHAT_CANCEL_GRACE_SECONDS": "cancel_grace_seconds",
    "AGENT_LEGION_STUDIO_CHAT_UNPROMPTED_IDLE_TIMEOUT_SECONDS": "unprompted_idle_timeout_seconds",
    "AGENT_LEGION_STUDIO_CHAT_CLOSE_GRACE_SECONDS": "close_grace_seconds",
}


@pytest.fixture(autouse=True)
def clean_studio_env(monkeypatch):
    monkeypatch.setenv("AGENT_LEGION_SKIP_DOTENV", "1")
    for name in ENV_FIELDS:
        monkeypatch.delenv(name, raising=False)


def config_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("data_dir: data\n", encoding="utf-8")
    return path


def test_defaults_preserve_existing_timeouts(tmp_path):
    settings = load_settings(data_dir=tmp_path / "data", config_path=config_file(tmp_path))

    assert settings.studio_timing == StudioTimingConfig(
        prompt_timeout_seconds=3600,
        cancel_grace_seconds=30,
        unprompted_idle_timeout_seconds=900,
        close_grace_seconds=5,
    )
    assert tuple(map(str, vars(settings.studio_timing).values())) == (
        "3600",
        "30",
        "900",
        "5",
    )


def test_valid_env_values_populate_immutable_timing(tmp_path, monkeypatch):
    expected = {}
    for index, (name, field) in enumerate(ENV_FIELDS.items(), start=1):
        monkeypatch.setenv(name, str(index + 0.25))
        expected[field] = index + 0.25

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_file(tmp_path))

    assert settings.studio_timing == StudioTimingConfig(**expected)
    with pytest.raises(FrozenInstanceError):
        settings.studio_timing.prompt_timeout_seconds = 1


def test_yaml_is_not_a_second_studio_timing_configuration_channel(tmp_path, monkeypatch):
    path = config_file(tmp_path)
    path.write_text("data_dir: data\nstudio_chat: {prompt_timeout_seconds: 12}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="env-only"):
        load_settings(data_dir=tmp_path / "data", config_path=path)

    monkeypatch.setenv("AGENT_LEGION_STUDIO_CHAT_PROMPT_TIMEOUT_SECONDS", "13")
    settings = load_settings(data_dir=tmp_path / "data", config_path=path)
    assert settings.studio_timing.prompt_timeout_seconds == 13


@pytest.mark.parametrize("field", list(ENV_FIELDS.values()))
@pytest.mark.parametrize("value", [True, False, 0, -1, float("nan"), float("inf"), float("-inf")])
def test_typed_config_rejects_non_positive_or_non_finite_values(field, value):
    with pytest.raises(ValueError, match=rf"studio_chat\.{field}"):
        StudioTimingConfig(**{field: value})


@pytest.mark.parametrize("name", list(ENV_FIELDS))
@pytest.mark.parametrize("value", ["", "garbage", "0", "-0.1", "nan", "inf", "-inf"])
def test_env_rejects_invalid_values_at_settings_load(tmp_path, monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        load_settings(data_dir=tmp_path / "data", config_path=config_file(tmp_path))
