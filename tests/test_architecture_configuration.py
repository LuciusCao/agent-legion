from pathlib import Path

from scripts.architecture.configuration import check_configuration_ownership


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_configuration_ownership_accepts_split_files(tmp_path):
    _write(tmp_path / "config/app.yaml", "data_dir: data\n")
    _write(tmp_path / "config/agent_legion.yaml", "cms: {}\n")
    _write(tmp_path / "config/workflow.yaml", "executors: {}\n")
    assert check_configuration_ownership(tmp_path) == []


def test_configuration_ownership_rejects_wrong_file_key(tmp_path):
    _write(tmp_path / "config/app.yaml", "cms: {}\n")
    _write(tmp_path / "config/agent_legion.yaml", "{}\n")
    _write(tmp_path / "config/workflow.yaml", "{}\n")
    errors = check_configuration_ownership(tmp_path)
    assert errors == ["config/app.yaml: top-level key 'cms' belongs to config/agent_legion.yaml"]


def test_configuration_ownership_rejects_partial_layout(tmp_path):
    _write(tmp_path / "config/app.yaml", "{}\n")
    assert "partial configuration layout" in check_configuration_ownership(tmp_path)[0]
