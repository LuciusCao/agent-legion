"""Node-code read/draft composition for the studio-agent tool surface.

Split from ``studio_agent_tools`` (budget): resolves the node's capability
from the active revision, applies the ``expected_capability`` contract
(existing node → validate, mismatch is a 400; unknown node → skeleton draft
only when the declaration is present), and mirrors the Studio read of
effective code plus any pending draft.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.node_codes import NodeCodeService
from server.app.workflows.definition import workflow_definition_from_dict
from server.app.workflows.start_node import START_NODE_TYPE

if TYPE_CHECKING:
    from server.app.jobs import JobQueries
    from server.app.settings import Settings


class StudioAgentNodeCodeTools:
    """Node-code tools behind the studio-agent surface (drafts/reads only)."""

    def __init__(self, job_db: JobQueries, settings: Settings) -> None:
        self._job_db = job_db
        self._settings = settings

    def _service(self) -> NodeCodeService:
        return NodeCodeService(
            self._job_db.path, self._settings.executor_runtime.workflows.custom_nodes_enabled
        )

    def _revision_node_capability(
        self, revision: dict[str, Any] | None, node_key: str
    ) -> str | None:
        if revision is None:
            return None
        definition = workflow_definition_from_dict(json.loads(str(revision["definition_json"])))
        node = definition.nodes.get(node_key)
        if node is None or node.node_type == START_NODE_TYPE:
            # Start nodes never execute: treat them as unknown so no orphan
            # node-code draft can be saved for them.
            return None
        return node.capability

    def _reject_start_node(self, workspace_id: str, workflow_key: str, node_key: str) -> None:
        """404 only for start nodes of the active revision (never execute, no
        code to read). Draft-only/skeleton nodes are readable so an agent can
        read back the draft it saved before the workflow revision exists."""
        revision = self._job_db.get_active_workflow_revision(workspace_id, workflow_key)
        if revision is None:
            return
        definition = workflow_definition_from_dict(json.loads(str(revision["definition_json"])))
        node = definition.nodes.get(node_key)
        if node is not None and node.node_type == START_NODE_TYPE:
            raise NotFoundError(f"Unknown workflow node: {node_key}")

    def save_draft(
        self,
        workspace_id: str,
        workflow_key: str,
        node_key: str,
        code: str,
        created_by: str,
        change_note: str | None,
        expected_capability: str | None = None,
    ) -> dict[str, Any]:
        revision = self._job_db.get_active_workflow_revision(workspace_id, workflow_key)
        capability = self._revision_node_capability(revision, node_key)
        if capability is None and expected_capability is None:
            # Historic 404s; with expected_capability set, a missing node is a
            # skeleton draft for a node the not-yet-published workflow draft
            # will introduce (no revision to validate against).
            raise NotFoundError(
                "No active workflow revision"
                if revision is None
                else f"Unknown workflow node: {node_key}"
            )
        if capability is not None and expected_capability not in (None, capability):
            raise InvalidOperationError(
                f"Node {node_key} capability mismatch: the active workflow revision "
                f"binds {capability!r}, but expected_capability declares {expected_capability!r}"
            )
        return self._service().save_draft(
            workspace_id, workflow_key, node_key, code, created_by, change_note
        )

    def get_state(self, workspace_id: str, workflow_key: str, node_key: str) -> dict[str, Any]:
        """Effective code plus any pending draft (mirrors the Studio read)."""
        self._reject_start_node(workspace_id, workflow_key, node_key)
        versions = self._service().list_versions(workspace_id, workflow_key, node_key)
        published = next((row for row in versions if row["status"] == "published"), None)
        # list_versions is version-descending: the first draft is the current one.
        draft = next((row for row in versions if row["status"] == "draft"), None)
        state: dict[str, Any] = {
            "has_draft": draft is not None,
            "draft_code": str(draft["code"]) if draft is not None else None,
            "draft_version": int(draft["version"]) if draft is not None else None,
        }
        if published is not None:
            if published["created_by"] == "system":
                return {**state, "origin": "builtin", "code": str(published["code"])}
            return {
                **state,
                "origin": "custom",
                "code": str(published["code"]),
                "version": int(published["version"]),
            }
        return {**state, "origin": "none", "code": ""}
