from pathlib import Path
from typing import Any

from server.app.storage_paths import resolve_data_path


def resolve_record_paths(
    record: dict[str, Any],
    data_dir: Path,
    path_fields: set[str],
) -> dict[str, Any]:
    """Return a shallow copy of ``record`` with selected paths resolved absolute.

    Empty optional path fields are left unchanged.
    """
    copied = dict(record)
    for field in path_fields:
        value = copied.get(field, "")
        if value:
            copied[field] = str(resolve_data_path(value, data_dir, allow_missing=True))
    return copied
