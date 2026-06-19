from __future__ import annotations

from pathlib import Path

from server.app.storage_paths import make_data_relative


def canonicalize_if_absolute(path: str, data_dir: Path | None) -> str:
    """Return a data-relative path if ``data_dir`` is set and ``path`` is absolute."""
    if data_dir is not None and path and Path(path).is_absolute():
        return make_data_relative(Path(path), data_dir)
    return path
