from pathlib import Path

from server.app.configuration.loader import (
    CONFIG_FILE_KEYS,
    ConfigurationLoadError,
    detect_layout,
    load_yaml_mapping,
)
from server.app.configuration.owned_keys import LEGACY_FILE_ALIASES


def check_configuration_ownership(root: Path) -> list[str]:
    config_dir = root / "config"
    split_files = [config_dir / name for name in CONFIG_FILE_KEYS]
    split_files += [config_dir / name for name in LEGACY_FILE_ALIASES]
    if not any(path.exists() for path in split_files):
        # Synthetic repositories in tests have no runtime config; there is
        # nothing to check until at least one split file exists.
        return []
    try:
        selection = detect_layout(config_dir)
    except ConfigurationLoadError as exc:
        return [str(exc)]
    owner_by_key = {key: name for name, keys in CONFIG_FILE_KEYS.items() for key in keys}
    errors: list[str] = []
    for path in selection.paths:
        try:
            mapping = load_yaml_mapping(path)
        except ConfigurationLoadError as exc:
            errors.append(str(exc))
            continue
        # Legacy file names own the same keys as their canonical replacement.
        file_name = LEGACY_FILE_ALIASES.get(path.name, path.name)
        for key in sorted(mapping):
            expected = owner_by_key.get(key)
            if expected is None:
                errors.append(f"config/{path.name}: unknown top-level key '{key}'")
            elif expected != file_name:
                errors.append(
                    f"config/{path.name}: top-level key '{key}' belongs to config/{expected}"
                )
    return errors
