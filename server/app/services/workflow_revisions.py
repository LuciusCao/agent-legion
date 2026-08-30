"""Workflow revision publication service (publish / runtime-save / seed).

Why this module is small (#287): the stateless publish pipeline (pins
freeze/embed, version allocation, route derivation) moved to
workflow_revision_pipeline.py, with the route derivation shared from
workflow_revision_routes.py. What remains here is the facade:
construction, demo seeding, and delegation, so callers keep one entry
point. The startup route reconcile retired with explicit node types
(#284): routes change only at revision publication.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from server.app.agent_catalog.builtin import (
    DEMO_WORKFLOW_KEY,
    seed_demo_workspace_agent_definitions,
)
from server.app.services.workflow_revision_pipeline import publish_workflow_revision
from server.app.services.workflow_revision_runtime import save_revision_runtime_or_publish
from server.app.workflows.definition import WorkflowDefinition

if TYPE_CHECKING:
    from server.app.jobs import JobQueries


class WorkflowRevisionService:
    def __init__(self, job_db: JobQueries, custom_nodes_enabled: bool = True) -> None:
        self.job_db = job_db
        self.custom_nodes_enabled = custom_nodes_enabled

    def publish_workspace_revision(self, workspace_id: str, definition: WorkflowDefinition) -> dict:
        return publish_workflow_revision(
            self.job_db, self.custom_nodes_enabled, workspace_id, definition
        )

    def save_workspace_revision(self, workspace_id: str, definition: WorkflowDefinition) -> dict:
        """Update runtime settings in-place, or publish a structural revision."""
        return save_revision_runtime_or_publish(
            self.job_db, workspace_id, definition, self.publish_workspace_revision
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
        if definition.key == DEMO_WORKFLOW_KEY:
            # Demo seed exception (schema v46): a workspace binding the
            # built-in demo workflow gets the factory agent templates
            # instantiated into its own catalog, seed-if-absent. Admin edits
            # inside the workspace are never overwritten.
            seed_demo_workspace_agent_definitions(self.job_db, workspace_id)
        return self.publish_workspace_revision(workspace_id, definition)
