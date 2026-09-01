from __future__ import annotations

from typing import TYPE_CHECKING

from server.app.services.workflow_drafts import (
    validate_workflow_definition,
    validate_workflow_for_publish,
    workflow_definition_from_yaml_string,
)
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.skill_repo_gate import skill_repo_publish_errors

if TYPE_CHECKING:
    from pathlib import Path

    from server.app.jobs import JobQueries


def validate_workflow_draft_for_publish(
    job_db: JobQueries,
    workspace_id: str,
    definition_yaml: str,
    custom_nodes_enabled: bool,
    skill_base_dir: Path | None = None,
) -> list[str]:
    """The full publish validation set (structure + resolvability + skill repos)."""
    errors = validate_workflow_definition(definition_yaml)
    if errors:
        return errors
    definition = workflow_definition_from_yaml_string(definition_yaml)
    return validate_workflow_for_publish(
        definition=definition,
        workspace_id=workspace_id,
        job_db=job_db,
        custom_nodes_enabled=custom_nodes_enabled,
    ) or skill_repo_publish_errors(definition, workspace_id, job_db, skill_base_dir)


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
    WorkflowRevisionService(job_db, custom_nodes_enabled).save_workspace_revision(
        workspace_id, workflow_definition_from_yaml_string(definition_yaml)
    )
    # Schema v62: the workflow key is bound to the workspace id at creation
    # and immutable — no first-publish adoption path anymore.
    return True, []
