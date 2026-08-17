"""Studio agent tool surface services (phase 3 chunk 3).

The studio-agent tool endpoints (``/api/studio-agent/tools/*``) expose only
draft/validate/register-request operations plus reads — never publish or
other effecting actions (STUDIO-AGENT-001). This module composes the existing
services behind that surface and stamps every draft it writes with
``created_by=f"studio-agent:{user_id}"`` so agent-authored drafts stay
attributable to the run's initiating user. Node-code reads/drafts live in
``studio_agent_node_codes`` (split for budget).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from server.app.agent_catalog import AgentDefinition
from server.app.services.agent_service import AgentService
from server.app.services.job_errors import NotFoundError
from server.app.services.studio_agent_node_codes import StudioAgentNodeCodeTools
from server.app.services.versioned_entities import VersionedEntity
from server.app.services.workflow_catalog import WorkflowCatalogService
from server.app.services.workflow_draft_compare import compare_workflow_draft
from server.app.services.workflow_draft_publish import validate_workflow_draft_for_publish
from server.app.services.workflow_revision_format import (
    definition_to_yaml,
    workflow_definition_to_response_payload,
)
from server.app.workflows.definition import workflow_definition_from_dict

if TYPE_CHECKING:
    from server.app.jobs import JobQueries
    from server.app.settings import Settings

STUDIO_AGENT_CREATED_BY_PREFIX = "studio-agent:"


def studio_agent_created_by(user_id: str) -> str:
    """Attribution marker for agent-authored drafts (decision §0.4)."""
    return f"{STUDIO_AGENT_CREATED_BY_PREFIX}{user_id}"


class StudioAgentToolsService:
    """Composes workflow/node-code/agent-definition services for the tool API."""

    def __init__(self, job_db: JobQueries, settings: Settings) -> None:
        self._job_db = job_db
        self._settings = settings
        self.node_codes = StudioAgentNodeCodeTools(job_db, settings)

    # Write tools (draft/register only — no effecting operations).

    def validate_workflow(self, workspace_id: str, definition_yaml: str) -> list[str]:
        """The full publish validation set (structure + bindings), no writes."""
        return validate_workflow_draft_for_publish(
            self._job_db, workspace_id, definition_yaml, self._settings.executor_definitions
        )

    def compare_workflow(self, workspace_id: str, definition_yaml: str) -> dict[str, Any]:
        if self._job_db.get_workspace(workspace_id) is None:
            raise NotFoundError("Workspace not found")
        # The tool surface degrades to a full-draft preview when the workflow
        # was never published (no baseline to diff against).
        return compare_workflow_draft(
            self._job_db, workspace_id, definition_yaml, allow_missing_baseline=True
        )

    def save_node_code_draft(
        self,
        workspace_id: str,
        workflow_key: str,
        node_key: str,
        code: str,
        change_note: str | None,
        user_id: str,
        expected_capability: str | None = None,
    ) -> dict[str, Any]:
        return self.node_codes.save_draft(
            workspace_id,
            workflow_key,
            node_key,
            code,
            studio_agent_created_by(user_id),
            change_note,
            expected_capability,
        )

    def save_agent_definition_draft(
        self, workspace_id: str, agent_id: str, definition: AgentDefinition, user_id: str
    ) -> VersionedEntity:
        return AgentService(self._job_db.path, workspace_id).save_draft(
            agent_id, definition, studio_agent_created_by(user_id)
        )

    def register_workflow(self, key: str, label: str, description: str) -> dict[str, Any]:
        """Register a catalog key; the caller triggers the scan hot-reload."""
        return WorkflowCatalogService(self._settings).register(key, label, description)

    # Read tools.

    def get_active_revision(self, workspace_id: str) -> dict[str, Any]:
        workspace = self._job_db.get_workspace(workspace_id)
        if workspace is None:
            raise NotFoundError("Workspace not found")
        workflow_key = str(workspace.get("default_workflow_key") or "")
        revision = None
        if workflow_key:
            revision = self._job_db.get_active_workflow_revision(workspace_id, workflow_key)
        if revision is None:
            # Structured empty state (no default workflow key, or no published
            # revision yet): the agent switches to the from-scratch flow.
            return {"state": "empty", "workflow_key": workflow_key or None}
        definition = workflow_definition_from_dict(json.loads(str(revision["definition_json"])))
        return {
            "state": "active",
            "workflow_key": workflow_key,
            "revision": dict(revision),
            "workflow": workflow_definition_to_response_payload(definition),
            "definition_yaml": definition_to_yaml(definition),
        }

    def list_catalog(self) -> list[dict[str, Any]]:
        return WorkflowCatalogService(self._settings).list_workflows()

    def get_node_code_state(
        self, workspace_id: str, workflow_key: str, node_key: str
    ) -> dict[str, Any]:
        """Effective code plus any pending draft (mirrors the Studio read)."""
        return self.node_codes.get_state(workspace_id, workflow_key, node_key)
