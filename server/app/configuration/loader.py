from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from server.app.configuration.owned_keys import (
    CONFIG_FILE_KEYS,
    LEGACY_FILE_ALIASES,
    owned_keys_for_file,
)

logger = logging.getLogger(__name__)

SPLIT_FILE_NAMES = tuple(CONFIG_FILE_KEYS)


class ConfigLayout(StrEnum):
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


def _legacy_name_for(name: str) -> str | None:
    for legacy, target in LEGACY_FILE_ALIASES.items():
        if target == name:
            return legacy
    return None


def detect_layout(config_dir: Path) -> LayoutSelection:
    """Select the split config files, honoring legacy file-name aliases.

    Config governance G4 renamed ``video_hive.yaml`` to ``agent_legion.yaml``.
    The legacy name keeps working during the transition window: it is loaded
    with a warning when it is the only one present, and rejected when both
    exist. Migrate by renaming the file to the canonical name.
    """
    paths: list[Path] = []
    present_names: list[str] = []
    missing_names: list[str] = []
    legacy_used: list[tuple[str, str]] = []
    for name in SPLIT_FILE_NAMES:
        canonical = config_dir / name
        legacy_name = _legacy_name_for(name)
        legacy = config_dir / legacy_name if legacy_name else None
        if legacy is not None and legacy.exists() and canonical.exists():
            raise ConfigurationLoadError(
                f"both {canonical} and {legacy} exist; migrate by renaming "
                f"{legacy.name} to {canonical.name} and deleting the old file"
            )
        if legacy is not None and legacy.exists():
            paths.append(legacy)
            present_names.append(legacy.name)
            legacy_used.append((legacy.name, name))
        elif canonical.exists():
            paths.append(canonical)
            present_names.append(canonical.name)
        else:
            paths.append(canonical)
            missing_names.append(canonical.name)
    if missing_names:
        raise ConfigurationLoadError(
            f"partial configuration layout: present={present_names}, missing={missing_names}"
        )
    for legacy_name, canonical_name in legacy_used:
        logger.warning(
            "loading legacy config file name %s; migrate by renaming it to %s (content unchanged)",
            legacy_name,
            canonical_name,
        )
    return LayoutSelection(ConfigLayout.SPLIT, tuple(paths))


def _format_yaml_error(path: Path, exc: yaml.YAMLError) -> str:
    mark = getattr(exc, "problem_mark", None)
    if mark is not None:
        return (
            f"invalid YAML in {path}: {exc.__class__.__name__} "
            f"at line {mark.line + 1}, column {mark.column + 1}"
        )
    return f"invalid YAML in {path}: {exc.__class__.__name__}"


def load_yaml_mapping(path: Path, *, allow_missing: bool = False) -> dict[str, Any]:
    if not path.exists():
        if allow_missing:
            return {}
        raise ConfigurationLoadError(f"configuration file does not exist: {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        # Do not chain the original YAMLError because its message contains the raw
        # source line and may leak secret configuration values.
        raise ConfigurationLoadError(_format_yaml_error(path, exc)) from None
    if not isinstance(loaded, dict):
        raise ConfigurationLoadError(f"configuration root must be a mapping: {path}")
    return loaded


def validate_owned_keys(path: Path, mapping: dict[str, Any]) -> None:
    owned = owned_keys_for_file(path.name)
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


def load_application_config(
    root_dir: Path,
    config_path: Path | None = None,
) -> LoadedConfig:
    if config_path is not None:
        mapping = load_yaml_mapping(config_path, allow_missing=True)
        return LoadedConfig(mapping, ConfigLayout.EXPLICIT, (config_path,))
    selection = detect_layout(root_dir / "config")
    sections = [(path, load_yaml_mapping(path)) for path in selection.paths]
    for path, mapping in sections:
        validate_owned_keys(path, mapping)
    return LoadedConfig(merge_config_sections(sections), ConfigLayout.SPLIT, selection.paths)
