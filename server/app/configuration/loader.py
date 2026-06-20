from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

CONFIG_FILE_KEYS: dict[str, frozenset[str]] = {
    "app.yaml": frozenset({"data_dir", "server", "worker"}),
    "video_hive.yaml": frozenset(
        {
            "asr",
            "cms",
            "resource_providers",
            "cleanup_video_after_assemble",
            "openclaw",
        }
    ),
    "workflow.yaml": frozenset({"executors", "workflows"}),
}
SPLIT_FILE_NAMES = tuple(CONFIG_FILE_KEYS)


class ConfigLayout(StrEnum):
    LEGACY = "legacy"
    SPLIT = "split"
    EXPLICIT = "explicit"


class ConfigurationLoadError(ValueError):
    pass


@dataclass(frozen=True)
class LayoutSelection:
    layout: ConfigLayout
    paths: tuple[Path, ...]


@dataclass(frozen=True)
class LoadedConfig:
    config: dict[str, Any]
    layout: ConfigLayout
    paths: tuple[Path, ...]


def detect_layout(config_dir: Path) -> LayoutSelection:
    paths = tuple(config_dir / name for name in SPLIT_FILE_NAMES)
    present = tuple(path for path in paths if path.exists())
    app_present = paths[0].exists()
    video_present = paths[1].exists()
    workflow_present = paths[2].exists()
    if not app_present and not video_present:
        return LayoutSelection(ConfigLayout.LEGACY, (paths[2],))
    if app_present and video_present and workflow_present:
        return LayoutSelection(ConfigLayout.SPLIT, paths)
    present_names = [path.name for path in present]
    missing_names = [path.name for path in paths if not path.exists()]
    raise ConfigurationLoadError(
        f"partial configuration layout: present={present_names}, missing={missing_names}"
    )


_legacy_warning_emitted = False


def load_yaml_mapping(path: Path, *, allow_missing: bool = False) -> dict[str, Any]:
    if not path.exists():
        if allow_missing:
            return {}
        raise ConfigurationLoadError(f"configuration file does not exist: {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationLoadError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigurationLoadError(f"configuration root must be a mapping: {path}")
    return loaded


def validate_owned_keys(path: Path, mapping: dict[str, Any]) -> None:
    owned = CONFIG_FILE_KEYS[path.name]
    invalid = sorted(set(mapping) - owned)
    if invalid:
        raise ConfigurationLoadError(f"{path.name} contains unowned top-level keys: {invalid}")


def merge_config_sections(sections: list[tuple[Path, dict[str, Any]]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for path, section in sections:
        duplicate = sorted(set(merged) & set(section))
        if duplicate:
            raise ConfigurationLoadError(f"duplicate top-level keys from {path}: {duplicate}")
        merged.update(section)
    return merged


def _warn_legacy_once(path: Path) -> None:
    global _legacy_warning_emitted
    if _legacy_warning_emitted:
        return
    logging.getLogger(__name__).warning(
        "legacy single-file configuration at %s is deprecated; run "
        "scripts/migrate-config-layout.py --check",
        path,
    )
    _legacy_warning_emitted = True


def load_application_config(
    root_dir: Path,
    config_path: Path | None = None,
) -> LoadedConfig:
    if config_path is not None:
        mapping = load_yaml_mapping(config_path, allow_missing=True)
        return LoadedConfig(mapping, ConfigLayout.EXPLICIT, (config_path,))
    selection = detect_layout(root_dir / "config")
    if selection.layout is ConfigLayout.LEGACY:
        path = selection.paths[0]
        mapping = load_yaml_mapping(path, allow_missing=True)
        if path.exists():
            _warn_legacy_once(path)
        return LoadedConfig(mapping, ConfigLayout.LEGACY, selection.paths)
    sections = [(path, load_yaml_mapping(path)) for path in selection.paths]
    for path, mapping in sections:
        validate_owned_keys(path, mapping)
    return LoadedConfig(merge_config_sections(sections), ConfigLayout.SPLIT, selection.paths)
