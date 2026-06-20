from pathlib import Path

from server.app.configuration.loader import (
    CONFIG_FILE_KEYS,
    ConfigLayout,
    ConfigurationLoadError,
    detect_layout,
    load_yaml_mapping,
)


def check_configuration_ownership(root: Path) -> list[str]:
    config_dir = root / "config"
    try:
        selection = detect_layout(config_dir)
    except ConfigurationLoadError as exc:
        return [str(exc)]
    if selection.layout is ConfigLayout.LEGACY:
        return []
    owner_by_key = {
        key: name for name, keys in CONFIG_FILE_KEYS.items() for key in keys
    }
    errors: list[str] = []
    for path in selection.paths:
        try:
            mapping = load_yaml_mapping(path)
        except ConfigurationLoadError as exc:
            errors.append(str(exc))
            continue
        for key in sorted(mapping):
            expected = owner_by_key.get(key)
            if expected is None:
                errors.append(f"config/{path.name}: unknown top-level key '{key}'")
            elif expected != path.name:
                errors.append(
                    f"config/{path.name}: top-level key '{key}' belongs to config/{expected}"
                )
    return errors
