from __future__ import annotations

from pathlib import Path

from server.app.storage_paths import ManagedPathError, make_data_relative, resolve_data_path


def canonicalize_data_path(path: str, data_dir: Path | None, expected_category: str) -> str:
    """Return a validated canonical path for a database path column."""
    if not path or data_dir is None:
        return path
    resolved = resolve_data_path(path, data_dir, allow_missing=True)
    relative = make_data_relative(resolved, data_dir)
    category = Path(relative).parts[0]
    if category != expected_category:
        raise ManagedPathError(
            f"Stored path starts with '{category}', expected '{expected_category}'",
            root_kind=expected_category,
        )
    return relative
