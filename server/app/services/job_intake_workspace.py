from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_errors import NotFoundError
from server.app.settings import Settings


def get_workspace(job_db: JobQueries, workspace_id: str) -> dict[str, Any]:
    workspace = job_db.get_workspace(workspace_id)
    if workspace is None:
        raise NotFoundError("Workspace not found")
    return workspace


def singular_field_name(value: str) -> str:
    if value.endswith("ies"):
        return f"{value[:-3]}y"
    if value.endswith("s"):
        return value[:-1]
    return value


def effective_cms_config(settings: Settings, workspace: dict[str, Any]) -> dict[str, Any]:
    base = settings.config.get("cms", {})
    config = dict(base) if isinstance(base, dict) else {}
    workspace_config = workspace.get("cms_config")
    if isinstance(workspace_config, dict):
        config.update(workspace_config)
    return config


def enabled_intake_modes(workspace: dict[str, Any]) -> set[str] | None:
    intake_config = workspace.get("intake_config")
    if not isinstance(intake_config, dict) or "enabled_modes" not in intake_config:
        return None
    enabled_modes = intake_config.get("enabled_modes")
    if not isinstance(enabled_modes, list):
        return None
    return {str(mode) for mode in enabled_modes}
