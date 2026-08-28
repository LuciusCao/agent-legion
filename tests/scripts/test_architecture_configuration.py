from pathlib import Path

import pytest

from scripts.architecture.configuration import check_configuration_ownership

pytestmark = pytest.mark.no_db


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_configuration_ownership_accepts_zero_split_files(tmp_path):
    """All runtime split files are retired: an empty config/ dir is clean."""
    assert check_configuration_ownership(tmp_path) == []


def test_configuration_ownership_rejects_retired_agent_legion_yaml(tmp_path):
    _write(tmp_path / "config/agent_legion.yaml", "asr: {}\n")
    errors = check_configuration_ownership(tmp_path)
    assert errors == [
        "config/agent_legion.yaml: retired configuration file (see loader reject_retired_files)"
    ]


def test_configuration_ownership_rejects_retired_workflow_yaml(tmp_path):
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
