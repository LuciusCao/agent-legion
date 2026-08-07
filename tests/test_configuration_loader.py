from pathlib import Path

import pytest

from server.app.configuration.loader import (
    ConfigLayout,
    ConfigurationLoadError,
    detect_layout,
    load_application_config,
    load_yaml_mapping,
    merge_config_sections,
    validate_owned_keys,
)


def _write(path: Path, text: str = "{}\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_detect_layout_accepts_complete_split(tmp_path: Path):
    for name in ("agent_legion.yaml", "workflow.yaml"):
        _write(tmp_path / name)
    assert detect_layout(tmp_path).layout is ConfigLayout.SPLIT


@pytest.mark.parametrize(
    "present",
    [
        set(),
        {"workflow.yaml"},
        {"agent_legion.yaml"},
    ],
)
def test_detect_layout_rejects_partial_split(tmp_path: Path, present: set[str]):
    for name in present:
        _write(tmp_path / name)
    with pytest.raises(ConfigurationLoadError) as exc_info:
        detect_layout(tmp_path)
    message = str(exc_info.value)
    assert "present=" in message
    assert "missing=" in message


def test_retired_app_yaml_is_rejected_with_migration_guidance(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write(config_dir / "app.yaml", "database: {url: postgresql://configured/app}\n")
    _write(config_dir / "agent_legion.yaml")
    _write(config_dir / "workflow.yaml")
    with pytest.raises(ConfigurationLoadError, match=r"retired.*app\.yaml"):
        load_application_config(tmp_path)


def test_retired_app_yaml_alone_is_rejected(tmp_path: Path):
    _write(tmp_path / "config" / "app.yaml", "data_dir: data\n")
    with pytest.raises(ConfigurationLoadError) as exc_info:
        load_application_config(tmp_path)
    message = str(exc_info.value)
    assert "AGENT_LEGION_DATABASE_URL" in message
    assert "AGENT_LEGION_DATA_DIR" in message
    assert "instance-settings" in message


def test_split_layout_merges_owned_keys(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write(config_dir / "agent_legion.yaml", "asr: {provider: auto}\n")
    _write(config_dir / "workflow.yaml", "executors: {}\n")
    loaded = load_application_config(tmp_path)
    assert loaded.layout is ConfigLayout.SPLIT
    assert loaded.config == {
        "asr": {"provider": "auto"},
        "executors": {},
    }


def test_split_layout_rejects_key_in_wrong_file(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write(config_dir / "workflow.yaml", "asr: {}\n")
    _write(config_dir / "agent_legion.yaml")
    with pytest.raises(ConfigurationLoadError, match="workflow.yaml.*asr"):
        load_application_config(tmp_path)


@pytest.mark.parametrize("text", ["", "[]\n", "value\n"])
def test_yaml_root_must_be_mapping(tmp_path: Path, text: str):
    path = tmp_path / "explicit.yaml"
    _write(path, text)
    with pytest.raises(ConfigurationLoadError, match="mapping"):
        load_application_config(tmp_path, config_path=path)


def test_explicit_path_accepts_flat_legacy_keys(tmp_path: Path):
    path = tmp_path / "custom.yaml"
    _write(path, "data_dir: custom\ncms: {token: value}\nworkflows: {enabled: false}\n")
    loaded = load_application_config(tmp_path, config_path=path)
    assert loaded.layout is ConfigLayout.EXPLICIT
    assert loaded.config["cms"]["token"] == "value"


def test_missing_config_files_rejected(tmp_path: Path):
    with pytest.raises(ConfigurationLoadError, match="partial configuration layout"):
        load_application_config(tmp_path)


def test_load_yaml_mapping_reports_invalid_yaml(tmp_path: Path):
    path = tmp_path / "broken.yaml"
    _write(path, "foo: bar: baz\n")
    with pytest.raises(ConfigurationLoadError) as exc_info:
        load_yaml_mapping(path)
    message = str(exc_info.value)
    assert "invalid YAML" in message
    assert path.name in message
    assert "line" in message.lower()


def test_load_yaml_mapping_does_not_leak_secret_in_error(tmp_path: Path):
    path = tmp_path / "secret.yaml"
    _write(path, "token: TOP_SECRET: broken\n")
    with pytest.raises(ConfigurationLoadError) as exc_info:
        load_yaml_mapping(path)
    message = str(exc_info.value)
    assert "invalid YAML" in message
    assert "TOP_SECRET" not in message
    assert "TOP_SECRET" not in str(exc_info.value.__cause__)


def test_validate_owned_keys_rejects_unowned_keys(tmp_path: Path):
    path = tmp_path / "workflow.yaml"
    _write(path, "{}")
    with pytest.raises(ConfigurationLoadError, match="workflow.yaml.*unowned"):
        validate_owned_keys(path, {"cms": {}, "unknown_key": []})


def test_merge_config_sections_rejects_duplicate_keys(tmp_path: Path):
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    with pytest.raises(ConfigurationLoadError, match="duplicate.*second.yaml.*shared"):
        merge_config_sections([(first, {"shared": 1}), (second, {"shared": 2})])
