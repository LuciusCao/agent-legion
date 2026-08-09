from pathlib import Path

from server.app.configuration.loader import (
    CONFIG_FILE_KEYS,
    ConfigurationLoadError,
    detect_layout,
    load_yaml_mapping,
)
from server.app.configuration.owned_keys import RETIRED_FILE_NAMES


def check_configuration_ownership(root: Path) -> list[str]:
    config_dir = root / "config"
    retired = [name for name in RETIRED_FILE_NAMES if (config_dir / name).exists()]
    if retired:
        return [
            f"config/{name}: retired configuration file (see loader reject_retired_files)"
            for name in retired
        ]
    split_files = [config_dir / name for name in CONFIG_FILE_KEYS]
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
        file_name = path.name
        for key in sorted(mapping):
            expected = owner_by_key.get(key)
            if expected is None:
                errors.append(f"config/{path.name}: unknown top-level key '{key}'")
            elif expected != file_name:
                errors.append(
                    f"config/{path.name}: top-level key '{key}' belongs to config/{expected}"
                )
    return errors
