import os

import pytest
from pydantic import ValidationError

from server.app.settings import load_env_file, load_settings


def test_load_env_file_preserves_quoted_secret_values(tmp_path, monkeypatch):
    monkeypatch.delenv("BASECMS_SECRET", raising=False)
    monkeypatch.setenv("BASECMS_TOKEN", "already-set")
    env_file = tmp_path / ".env"
    env_file.write_text(
        'BASECMS_TOKEN="from-file"\nBASECMS_SECRET="fake#secret$value"\n',
        encoding="utf-8",
    )

    load_env_file(env_file)

    assert os.environ["BASECMS_TOKEN"] == "already-set"
    assert os.environ["BASECMS_SECRET"] == "fake#secret$value"


def test_load_settings_rejects_malformed_executor_yaml(tmp_path, monkeypatch):
    config_path = tmp_path / "pipeline.yaml"
    config_path.write_text(
        "data_dir: data\n"
        "executors:\n"
        "  bad-exec:\n"
        "    kind: local\n"
        "    global_capacity: 0\n"
        "    capabilities: {}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as exc_info:
        load_settings(data_dir=tmp_path / "data", config_path=config_path)

    message = str(exc_info.value)
    assert "bad-exec" in message
    assert "global_capacity" in message


def test_load_settings_exposes_executor_definitions(tmp_path, monkeypatch):
    config_path = tmp_path / "pipeline.yaml"
    config_path.write_text(
        "data_dir: data\n"
        "executors:\n"
        "  local-default:\n"
        "    kind: local\n"
        "    global_capacity: 4\n"
        "    capabilities:\n"
        "      fetch_questions:\n"
        "        handler: reading_analysis.fetch_questions\n",
        encoding="utf-8",
    )

    settings = load_settings(data_dir=tmp_path / "data", config_path=config_path)

    assert "local-default" in settings.executor_definitions
    assert settings.executor_definitions["local-default"].kind == "local"
    assert settings.executor_definitions["local-default"].global_capacity == 4
