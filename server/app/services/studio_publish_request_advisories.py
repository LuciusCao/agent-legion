"""Read-side skill-contract advisories for workflow publish requests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.services.studio_publish_request_support import with_draft_warnings
from server.app.services.workflow_drafts import workflow_definition_from_yaml_string
from server.app.workflows.skill_repo_gate import skill_repo_publish_warnings

if TYPE_CHECKING:
    from server.app.jobs import JobQueries


def draft_warnings(job_db: JobQueries, workspace_id: str, draft_yaml: str) -> list[str]:
    """Compute non-blocking advisories from the current draft and skill repos."""
    try:
        definition = workflow_definition_from_yaml_string(draft_yaml)
    except Exception:
        # #204 broad-except audit: display-only enrichment degrades to no
        # advisories if a concurrent edit made the draft unparseable; publish
        # validation remains the error channel.
        return []
    return skill_repo_publish_warnings(definition, workspace_id, job_db)


def with_current_draft_warnings(
    job_db: JobQueries, workspace_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Attach advisories from the workspace's current draft to a wire payload."""
    draft = job_db.get_workspace_workflow_draft(workspace_id)
    warnings = (
        draft_warnings(job_db, workspace_id, str(draft["definition_yaml"]))
        if draft is not None
        else []
    )
    return with_draft_warnings(payload, warnings)
