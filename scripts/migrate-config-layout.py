#!/usr/bin/env python3
# ruff: noqa: E402
"""Split Video Hive runtime configuration by domain."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

# Make the project root importable when the script is invoked directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.app.configuration.loader import (
    CONFIG_FILE_KEYS,
    SPLIT_FILE_NAMES,
    ConfigLayout,
    ConfigurationLoadError,
    detect_layout,
    load_yaml_mapping,
    merge_config_sections,
)


@dataclass(frozen=True)
class MigrationReport:
    status: str
    keys_by_file: dict[str, tuple[str, ...]]
    warnings: tuple[str, ...] = ()

    def render(self) -> str:
        lines = [f"status: {self.status}"]
        for name in sorted(self.keys_by_file):
            keys = ", ".join(sorted(self.keys_by_file[name]))
            lines.append(f"{name}: {keys}")
        for warning in self.warnings:
            lines.append(f"WARNING: {warning}")
        return "\n".join(lines)


def _owned_sections(source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    unknown = sorted(set(source) - set().union(*CONFIG_FILE_KEYS.values()))
    if unknown:
        raise ConfigurationLoadError(f"unknown top-level keys: {unknown}")
    return {
        name: {key: source[key] for key in source if key in owned}
        for name, owned in CONFIG_FILE_KEYS.items()
    }


def _write_staged(path: Path, mapping: dict[str, Any], mode: int) -> Path:
    fd, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    staged = Path(raw_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            yaml.safe_dump(mapping, stream, sort_keys=False, allow_unicode=True)
            stream.flush()
            os.fsync(stream.fileno())
        staged.chmod(mode)
        load_yaml_mapping(staged)
        return staged
    except Exception:
        staged.unlink(missing_ok=True)
        raise


def check_layout(config_dir: Path) -> MigrationReport:
    selection = detect_layout(config_dir)
    if selection.layout is ConfigLayout.SPLIT:
        keys_by_file: dict[str, tuple[str, ...]] = {}
        for path in selection.paths:
            mapping = load_yaml_mapping(path)
            validate_owned_keys(path, mapping)
            keys_by_file[path.name] = tuple(sorted(mapping))
        return MigrationReport("split", keys_by_file)

    source_path = selection.paths[0]
    source = load_yaml_mapping(source_path, allow_missing=True)
    sections = _owned_sections(source)
    return MigrationReport(
        "legacy",
        {name: tuple(sorted(section)) for name, section in sections.items()},
    )


def validate_owned_keys(path: Path, mapping: dict[str, Any]) -> None:
    owned = CONFIG_FILE_KEYS[path.name]
    invalid = sorted(set(mapping) - owned)
    if invalid:
        raise ConfigurationLoadError(f"{path.name} contains unowned top-level keys: {invalid}")


def _default_now() -> datetime:
    return datetime.now(UTC)


def _replace_in_order(
    staged_by_name: dict[str, Path],
    targets: dict[str, Path],
    before_replace: Callable[[Path], None] | None,
) -> None:
    for name in SPLIT_FILE_NAMES:
        target = targets[name]
        if before_replace is not None:
            before_replace(target)
        staged_by_name[name].replace(target)


def _sections_from_backup(backup_path: Path) -> dict[str, dict[str, Any]]:
    source = load_yaml_mapping(backup_path)
    return _owned_sections(source)


def _file_matches_recovery_state(
    path: Path, expected_section: dict[str, Any], full_backup: dict[str, Any] | None
) -> bool:
    """Return True when an existing file is in a state migration could have produced.

    A generated domain file must equal the expected backup section exactly. The legacy
    workflow.yaml source file may still contain the full backup content and is also
    accepted so an interrupted replacement of workflow.yaml can resume.
    """
    actual = load_yaml_mapping(path)
    return actual == expected_section or (
        full_backup is not None and path.name == "workflow.yaml" and actual == full_backup
    )


def _find_matching_backup(
    config_dir: Path, existing_names: set[str]
) -> tuple[Path, dict[str, dict[str, Any]]]:
    backups = sorted(config_dir.glob("workflow.yaml.bak-*"))
    if not backups:
        raise ConfigurationLoadError(
            "partial configuration layout and no workflow.yaml backup found for recovery"
        )

    matches: list[tuple[Path, dict[str, dict[str, Any]]]] = []
    for backup in backups:
        sections = _sections_from_backup(backup)
        full_backup = load_yaml_mapping(backup)
        if all(
            _file_matches_recovery_state(config_dir / name, sections[name], full_backup)
            for name in existing_names
        ):
            matches.append((backup, sections))

    if len(matches) != 1:
        backup_list = ", ".join(b.name for b in backups)
        raise ConfigurationLoadError(
            f"partial configuration layout; rollback required: cannot select a unique backup "
            f"for recovery (found {len(matches)} of {len(backups)} matching). "
            f"Backups: {backup_list}. "
            f"Restore config/workflow.yaml from a backup and remove generated domain files, "
            f"or run with --apply after ensuring the layout is consistent."
        )
    return matches[0]


def apply_layout(
    config_dir: Path,
    now: Callable[[], datetime] | None = None,
    before_replace: Callable[[Path], None] | None = None,
) -> MigrationReport:
    now = now or _default_now
    try:
        selection = detect_layout(config_dir)
    except ConfigurationLoadError:
        selection = None

    existing_names = {
        path.name for path in (config_dir / name for name in SPLIT_FILE_NAMES) if path.exists()
    }

    if selection is not None and selection.layout is ConfigLayout.SPLIT:
        # A clean split layout is a no-op. If ownership validation fails, treat it
        # as an interrupted migration and try to recover from a matching backup.
        try:
            keys_by_file: dict[str, tuple[str, ...]] = {}
            for path in selection.paths:
                mapping = load_yaml_mapping(path)
                validate_owned_keys(path, mapping)
                keys_by_file[path.name] = tuple(sorted(mapping))
            return MigrationReport("split", keys_by_file)
        except ConfigurationLoadError:
            pass

    if selection is not None and selection.layout is ConfigLayout.LEGACY:
        # Legacy single-file layout: back up, stage, validate, and replace.
        source_path = selection.paths[0]
        source = load_yaml_mapping(source_path)
        sections = _owned_sections(source)
        backup_path = source_path.with_name(f"{source_path.name}.bak-{now():%Y%m%d%H%M%S}")

        def _create_backup() -> None:
            shutil.copy2(source_path, backup_path)

        try:
            return _write_split_files(
                config_dir,
                sections,
                source_mode=source_path.stat().st_mode,
                before_replace=before_replace,
                backup_callback=_create_backup,
            )
        except Exception:
            # Do not delete backups; the operator may need them for recovery.
            raise

    # Partial or inconsistent split layout: recover from a unique matching backup.
    if not existing_names:
        raise ConfigurationLoadError("no configuration files found")
    backup, sections = _find_matching_backup(config_dir, existing_names)
    return _write_split_files(
        config_dir,
        sections,
        source_mode=backup.stat().st_mode,
        before_replace=before_replace,
    )


_COMMENT_LOSS_WARNING = (
    "generated YAML files do not preserve comments or formatting from the source configuration"
)


def _write_split_files(
    config_dir: Path,
    sections: dict[str, dict[str, Any]],
    source_mode: int,
    before_replace: Callable[[Path], None] | None,
    backup_callback: Callable[[], None] | None = None,
) -> MigrationReport:
    targets = {name: config_dir / name for name in SPLIT_FILE_NAMES}
    staged_by_name: dict[str, Path] = {}
    try:
        for name in SPLIT_FILE_NAMES:
            staged_by_name[name] = _write_staged(targets[name], sections[name], source_mode)
        loaded_sections = [
            (targets[name], load_yaml_mapping(staged_by_name[name])) for name in SPLIT_FILE_NAMES
        ]
        merge_config_sections(loaded_sections)
        if backup_callback is not None:
            backup_callback()
        print(f"WARNING: {_COMMENT_LOSS_WARNING}", file=sys.stderr)
        _replace_in_order(staged_by_name, targets, before_replace)
    finally:
        for staged in staged_by_name.values():
            staged.unlink(missing_ok=True)

    return MigrationReport(
        "split",
        {name: tuple(sorted(sections[name])) for name in SPLIT_FILE_NAMES},
        warnings=(_COMMENT_LOSS_WARNING,),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Split Video Hive runtime configuration by domain."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=PROJECT_ROOT / "config",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    try:
        report = apply_layout(args.config_dir) if args.apply else check_layout(args.config_dir)
    except ConfigurationLoadError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(report.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
