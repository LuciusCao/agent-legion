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


def validate_workflow_draft_for_publish(
    job_db: JobQueries,
    workspace_id: str,
    definition_yaml: str,
    settings_executor_definitions: dict[str, Any],
) -> list[str]:
    """The full publish validation set (structure + bindings), no writes."""
    errors = validate_workflow_definition(definition_yaml)
    if errors:
        return errors
    return validate_workflow_for_publish(
        definition=workflow_definition_from_yaml_string(definition_yaml),
        workspace_id=workspace_id,
        job_db=job_db,
        settings_executor_definitions=settings_executor_definitions,
    )


def publish_workflow_draft(
    job_db: JobQueries,
    workspace_id: str,
    definition_yaml: str,
    settings_executor_definitions: dict[str, Any],
    custom_nodes_enabled: bool = True,
) -> tuple[bool, list[str]]:
    errors = validate_workflow_draft_for_publish(
        job_db, workspace_id, definition_yaml, settings_executor_definitions
    )
    if errors:
        return False, errors
    definition = workflow_definition_from_yaml_string(definition_yaml)
    WorkflowRevisionService(job_db, custom_nodes_enabled).save_workspace_revision(
        workspace_id, definition
    )
    return True, []
