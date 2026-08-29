"""Workspace preview settings helpers (schema v63 preview_config_json).

Split from services/workspace_configuration.py for the architecture file
budget; the stored shape is ``{"hidden": [<artifact name>, ...]}`` and the
settings payload exposes it as ``previewHidden``.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_errors import InvalidOperationError


def clean_preview_hidden(patch: dict[str, Any]) -> list[str] | None:
    """Normalize the previewHidden patch: None = 未改；否则去重排序。"""
    hidden = patch.get("previewHidden")
    if hidden is None:
        return None
    if not isinstance(hidden, list) or not all(isinstance(v, str) for v in hidden):
        raise InvalidOperationError("previewHidden must be a list of artifact names")
    return sorted(set(hidden))


def apply_preview_hidden(hidden: list[str]) -> dict[str, Any]:
    """Build the stored preview_config value from a normalized hidden list."""
    return {"hidden": hidden}


def write_preview_hidden(
    job_db: JobQueries, workspace_id: str, hidden: list[str]
) -> dict[str, Any]:
    """Persist a normalized hidden list and return the updated workspace record."""
    return job_db.update_workspace(workspace_id, preview_config=apply_preview_hidden(hidden))
