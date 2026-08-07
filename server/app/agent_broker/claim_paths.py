"""``log_path`` canonicalization for the agent claim insert (issue #37).

Manifests frozen before relative-path enforcement may still hold absolute
``log_path`` values; heal them at the claim boundary so ``node_runs`` only
ever stores data-dir-relative paths.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from server.app.executors._path_canonicalization import canonicalize_data_path
from server.app.storage_paths import ManagedPathError


def claim_log_path(manifest: Mapping[str, Any], data_dir: Path | None) -> str:
    """Canonical ``log_path`` for the ``node_runs`` insert at agent claim time.

    Unmappable legacy values pass through unchanged: finish canonicalizes
    again and fails the run with a clear error there, instead of crashing
    the claim scan — one poisoned request must not block the queue head.
    """
    log_path = str(manifest.get("log_path", ""))
    if not log_path or data_dir is None:
        return log_path
    try:
        return canonicalize_data_path(log_path, data_dir, "logs")
    except ManagedPathError:
        return log_path
