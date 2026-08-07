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


def test_detect_layout_accepts_zero_split_files(tmp_path: Path):
    """Every runtime split file is retired: the canonical layout is empty."""
    selection = detect_layout(tmp_path)
    assert selection.layout is ConfigLayout.SPLIT
    assert selection.paths == ()


def test_empty_split_layout_loads_empty_config(tmp_path: Path):
    loaded = load_application_config(tmp_path)
    assert loaded.layout is ConfigLayout.SPLIT
    assert loaded.config == {}


def test_retired_app_yaml_is_rejected_with_migration_guidance(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write(config_dir / "app.yaml", "database: {url: postgresql://configured/app}\n")
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


def test_retired_workflow_yaml_is_rejected_with_migration_guidance(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write(config_dir / "workflow.yaml", "executors: {}\n")
    with pytest.raises(ConfigurationLoadError) as exc_info:
        load_application_config(tmp_path)
    message = str(exc_info.value)
    assert "workflow.yaml" in message
    assert "versioned_entities" in message
    assert "Studio" in message


def test_retired_agent_legion_yaml_is_rejected_with_migration_guidance(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write(config_dir / "agent_legion.yaml", "asr: {provider: auto}\n")
    with pytest.raises(ConfigurationLoadError) as exc_info:
        load_application_config(tmp_path)
    message = str(exc_info.value)
    assert "agent_legion.yaml" in message
    assert "transcribe_video" in message
    assert "AGENT_LEGION_ASR_WHISPER_BINARY" in message
    assert "AGENT_LEGION_ASR_SENSEVOICE_MODEL_DIR" in message


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


def test_explicit_path_ignores_neighbor_split_layout(tmp_path: Path):
    explicit = tmp_path / "custom.yaml"
    _write(explicit, "data_dir: selected\n")
    loaded = load_application_config(tmp_path, config_path=explicit)
    assert loaded.config["data_dir"] == "selected"


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
    path = tmp_path / "agent_legion.yaml"
    _write(path, "{}")
    with pytest.raises(ConfigurationLoadError, match="agent_legion.yaml.*unowned"):
        validate_owned_keys(path, {"cms": {}, "unknown_key": []})


def test_merge_config_sections_rejects_duplicate_keys(tmp_path: Path):
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    with pytest.raises(ConfigurationLoadError, match="duplicate.*second.yaml.*shared"):
        merge_config_sections([(first, {"shared": 1}), (second, {"shared": 2})])
