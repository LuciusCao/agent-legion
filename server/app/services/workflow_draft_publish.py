from __future__ import annotations

from typing import TYPE_CHECKING

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
    custom_nodes_enabled: bool,
) -> list[str]:
    """The full publish validation set (structure + code resolvability)."""
    errors = validate_workflow_definition(definition_yaml)
    if errors:
        return errors
    return validate_workflow_for_publish(
        definition=workflow_definition_from_yaml_string(definition_yaml),
        workspace_id=workspace_id,
        job_db=job_db,
        custom_nodes_enabled=custom_nodes_enabled,
    )


def publish_workflow_draft(
    job_db: JobQueries,
    workspace_id: str,
    definition_yaml: str,
    custom_nodes_enabled: bool = True,
) -> tuple[bool, list[str]]:
    errors = validate_workflow_draft_for_publish(
        job_db, workspace_id, definition_yaml, custom_nodes_enabled
    )
    if errors:
        return False, errors
    definition = workflow_definition_from_yaml_string(definition_yaml)
    WorkflowRevisionService(job_db, custom_nodes_enabled).save_workspace_revision(
        workspace_id, definition
    )
    # Schema v61: the workflow key is bound to the workspace id at creation
    # and immutable — no first-publish adoption path anymore.
    return True, []
