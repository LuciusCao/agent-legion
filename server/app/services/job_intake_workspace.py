from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_errors import NotFoundError


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
