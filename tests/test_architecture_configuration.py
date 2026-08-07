from pathlib import Path

import pytest

from scripts.architecture.configuration import check_configuration_ownership

pytestmark = pytest.mark.no_db


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_configuration_ownership_accepts_split_files(tmp_path):
    _write(tmp_path / "config/agent_legion.yaml", "asr: {}\n")
    assert check_configuration_ownership(tmp_path) == []


def test_configuration_ownership_rejects_unknown_key(tmp_path):
    _write(tmp_path / "config/agent_legion.yaml", "executors: {}\n")
    errors = check_configuration_ownership(tmp_path)
    assert errors == ["config/agent_legion.yaml: unknown top-level key 'executors'"]


def test_configuration_ownership_rejects_retired_workflow_yaml(tmp_path):
    _write(tmp_path / "config/agent_legion.yaml", "{}\n")
    _write(tmp_path / "config/workflow.yaml", "executors: {}\n")
    errors = check_configuration_ownership(tmp_path)
    assert errors == [
        "config/workflow.yaml: retired configuration file (see loader reject_retired_files)"
    ]


def test_configuration_ownership_rejects_retired_app_yaml(tmp_path):
    _write(tmp_path / "config/app.yaml", "data_dir: data\n")
    errors = check_configuration_ownership(tmp_path)
    assert errors == [
        "config/app.yaml: retired configuration file (see loader reject_retired_files)"
    ]
