from __future__ import annotations

from typing import TYPE_CHECKING

from server.app.services.workflow_revision_format import (
    definition_hash,
    serialize_definition,
)
from server.app.workflows.definition import WorkflowDefinition

if TYPE_CHECKING:
    from server.app.jobs import JobQueries


class WorkflowRevisionService:
    def __init__(self, job_db: JobQueries) -> None:
        self.job_db = job_db

    def publish_workspace_revision(self, workspace_id: str, definition: WorkflowDefinition) -> dict:
        definition_json = serialize_definition(definition)
        version = self.job_db.next_workflow_revision_version(workspace_id, definition.key)
        revision_id = f"{workspace_id}:{definition.key}:v{version}"
        return self.job_db.create_workflow_revision(
            revision_id=revision_id,
            workspace_id=workspace_id,
            workflow_key=definition.key,
            version=version,
            status="active",
            definition_json=definition_json,
            definition_hash=definition_hash(definition_json),
        )

    def get_active(self, workspace_id: str, workflow_key: str) -> dict:
        revision = self.job_db.get_active_workflow_revision(workspace_id, workflow_key)
        if revision is None:
            raise ValueError(f"No active workflow revision for {workflow_key}")
        return revision

    def ensure_active_revision(self, workspace_id: str, definition: WorkflowDefinition) -> dict:
        existing = self.job_db.get_active_workflow_revision(workspace_id, definition.key)
        if existing is not None:
            return existing
        return self.publish_workspace_revision(workspace_id, definition)
