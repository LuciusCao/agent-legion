from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.services.workflow_drafts import (
    validate_workflow_definition,
    validate_workflow_for_publish,
    workflow_definition_from_yaml_string,
)
from server.app.services.workflow_revisions import WorkflowRevisionService

if TYPE_CHECKING:
    from server.app.jobs import JobQueries


def publish_workflow_draft(
    job_db: JobQueries,
    workspace_id: str,
    definition_yaml: str,
    settings_executor_definitions: dict[str, Any],
) -> tuple[bool, list[str]]:
    errors = validate_workflow_definition(definition_yaml)
    if errors:
        return False, errors
    definition = workflow_definition_from_yaml_string(definition_yaml)
    errors = validate_workflow_for_publish(
        definition=definition,
        workspace_id=workspace_id,
        job_db=job_db,
        settings_executor_definitions=settings_executor_definitions,
    )
    if errors:
        return False, errors
    WorkflowRevisionService(job_db).save_workspace_revision(workspace_id, definition)
    return True, []
