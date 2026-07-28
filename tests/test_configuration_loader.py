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
    for name in ("app.yaml", "agent_legion.yaml", "workflow.yaml"):
        _write(tmp_path / name)
    assert detect_layout(tmp_path).layout is ConfigLayout.SPLIT


@pytest.mark.parametrize(
    "present",
    [
        set(),
        {"workflow.yaml"},
        {"app.yaml"},
        {"agent_legion.yaml"},
        {"app.yaml", "workflow.yaml"},
        {"agent_legion.yaml", "workflow.yaml"},
        {"app.yaml", "agent_legion.yaml"},
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


def test_split_layout_merges_owned_keys(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write(config_dir / "app.yaml", "data_dir: data\nserver: {port: 8000}\n")
    _write(config_dir / "agent_legion.yaml", "asr: {provider: auto}\n")
    _write(config_dir / "workflow.yaml", "workflows: {enabled: true}\n")
    loaded = load_application_config(tmp_path)
    assert loaded.layout is ConfigLayout.SPLIT
    assert loaded.config == {
        "data_dir": "data",
        "server": {"port": 8000},
        "asr": {"provider": "auto"},
        "workflows": {"enabled": True},
    }


def test_split_layout_rejects_key_in_wrong_file(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write(config_dir / "app.yaml", "cms: {}\n")
    _write(config_dir / "agent_legion.yaml")
    _write(config_dir / "workflow.yaml")
    with pytest.raises(ConfigurationLoadError, match="app.yaml.*cms"):
        load_application_config(tmp_path)


# --- Legacy video_hive.yaml transition window (config governance G4) --------


def test_legacy_video_hive_yaml_loads_with_warning(tmp_path: Path, caplog):
    config_dir = tmp_path / "config"
    _write(config_dir / "app.yaml", "data_dir: data\n")
    _write(config_dir / "video_hive.yaml", "asr: {provider: auto}\n")
    _write(config_dir / "workflow.yaml", "workflows: {enabled: true}\n")
    with caplog.at_level("WARNING", logger="server.app.configuration.loader"):
        loaded = load_application_config(tmp_path)
    assert loaded.layout is ConfigLayout.SPLIT
    assert loaded.config["asr"] == {"provider": "auto"}
    assert any(path.name == "video_hive.yaml" for path in loaded.paths)
    assert "video_hive.yaml" in caplog.text
    assert "agent_legion.yaml" in caplog.text


def test_legacy_video_hive_yaml_counts_toward_layout_completeness(tmp_path: Path):
    # Only the legacy middle file exists: it covers the agent_legion.yaml slot,
    # so the partial-layout error reports it as present (not missing).
    _write(tmp_path / "video_hive.yaml")
    with pytest.raises(ConfigurationLoadError) as exc_info:
        detect_layout(tmp_path)
    message = str(exc_info.value)
    assert "present=['video_hive.yaml']" in message
    assert "missing=['app.yaml', 'workflow.yaml']" in message


def test_legacy_and_canonical_names_conflict(tmp_path: Path):
    for name in ("app.yaml", "agent_legion.yaml", "video_hive.yaml", "workflow.yaml"):
        _write(tmp_path / name)
    with pytest.raises(ConfigurationLoadError, match="renaming"):
        detect_layout(tmp_path)


def test_legacy_file_obeys_owned_keys(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write(config_dir / "app.yaml")
    _write(config_dir / "video_hive.yaml", "workflows: {}\n")
    _write(config_dir / "workflow.yaml")
    with pytest.raises(ConfigurationLoadError, match="video_hive.yaml.*unowned"):
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
    path = tmp_path / "app.yaml"
    _write(path, "{}")
    with pytest.raises(ConfigurationLoadError, match="app.yaml.*unowned"):
        validate_owned_keys(path, {"cms": {}, "unknown_key": []})


def test_merge_config_sections_rejects_duplicate_keys(tmp_path: Path):
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    with pytest.raises(ConfigurationLoadError, match="duplicate.*second.yaml.*shared"):
        merge_config_sections([(first, {"shared": 1}), (second, {"shared": 2})])
