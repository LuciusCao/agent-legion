from __future__ import annotations

from typing import TYPE_CHECKING

from server.app.services.job_errors import DraftWorkflowKeyMismatchError, NotFoundError
from server.app.services.workflow_drafts import workflow_definition_from_yaml_string
from server.app.workflows.definition import WorkflowDefinitionError

if TYPE_CHECKING:
    from server.app.jobs import JobQueries


def require_draft_workflow_key_match(
    job_db: JobQueries, workspace_id: str, definition_yaml: str
) -> None:
    """Publish-side guard: the draft must target the workspace default workflow.

    Compare already rejects a foreign key as a schema error, but publish had no
    such check; close that gap. Unparseable YAML is left to the publish
    validation set (reported as draft errors); a parseable draft whose key does
    not match the workspace default_workflow_key is rejected with 422.
    """
    workspace = job_db.get_workspace(workspace_id)
    if workspace is None:
        raise NotFoundError("Workspace not found")
    try:
        draft_key = workflow_definition_from_yaml_string(definition_yaml).key
    except WorkflowDefinitionError:
        return
    default_key = str(workspace.get("default_workflow_key") or "")
    # Schema v61: the key is bound at creation (id == key), so the match
    # guard is unconditional; the empty-key branch only exists for databases
    # still mid-upgrade to v61.
    if not default_key:
        return
    if draft_key != default_key:
        raise DraftWorkflowKeyMismatchError(
            f"Draft workflow key '{draft_key}' does not match "
            f"workspace default workflow key '{default_key}'"
        )
