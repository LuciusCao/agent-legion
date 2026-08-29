"""Service layer for the Studio workflow YAML draft store (schema v61).

Thin pass-through over JobQueries (BOUNDARY-DATA-001): the only business
rule here is that the workspace must exist (404, mirroring the workflow
revisions routes) — the draft row itself is created by the upsert.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_errors import NotFoundError


def get_workflow_draft(job_db: JobQueries, workspace_id: str) -> dict[str, Any] | None:
    if job_db.get_workspace(workspace_id) is None:
        raise NotFoundError("Workspace not found")
    return job_db.get_workspace_workflow_draft(workspace_id)


def save_workflow_draft(
    job_db: JobQueries, workspace_id: str, definition_yaml: str
) -> dict[str, Any]:
    if job_db.get_workspace(workspace_id) is None:
        raise NotFoundError("Workspace not found")
    return job_db.upsert_workspace_workflow_draft(workspace_id, definition_yaml)
