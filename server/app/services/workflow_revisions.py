from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from server.app import agent_catalog_builtin
from server.app.services.agent_service import published_agent_definitions
from server.app.services.node_code_resolution import freeze_node_code_versions
from server.app.services.workflow_revision_format import (
    definition_hash,
    serialize_definition,
)
from server.app.services.workflow_revision_runtime import save_revision_runtime_or_publish
from server.app.workflows.definition import WorkflowDefinition, workflow_definition_from_dict

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)


class WorkflowRevisionService:
    def __init__(self, job_db: JobQueries, custom_nodes_enabled: bool = True) -> None:
        self.job_db = job_db
        self.custom_nodes_enabled = custom_nodes_enabled

    def publish_workspace_revision(self, workspace_id: str, definition: WorkflowDefinition) -> dict:
        definition_json = serialize_definition(definition)
        # node_code_pins snapshot the published custom code versions at publish
        # time (EXEC-CODE-002, design §4): they are publish-moment state, not
        # part of the workflow definition, so they ride alongside the
        # definition inside the stored definition_json but stay out of
        # definition_hash (computed from the pure definition above).
        pins = freeze_node_code_versions(
            self.job_db.path,
            self.custom_nodes_enabled,
            workspace_id,
            definition.key,
            [node.key for node in definition.nodes.values()],
        )
        stored_json = definition_json
        if pins:
            payload = json.loads(definition_json)
            payload["node_code_pins"] = pins
            stored_json = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        version = self.job_db.next_workflow_revision_version(workspace_id, definition.key)
        revision_id = f"{workspace_id}:{definition.key}:v{version}"
        agent_routes = self._agent_routes(workspace_id, definition)
        return self.job_db.create_workflow_revision(
            revision_id=revision_id,
            workspace_id=workspace_id,
            workflow_key=definition.key,
            version=version,
            status="active",
            definition_json=stored_json,
            definition_hash=definition_hash(definition_json),
            agent_routes=agent_routes,
        )

    def save_workspace_revision(self, workspace_id: str, definition: WorkflowDefinition) -> dict:
        """Update runtime settings in-place, or publish a structural revision."""
        return save_revision_runtime_or_publish(
            self.job_db, workspace_id, definition, self.publish_workspace_revision
        )

    def _agent_routes(self, workspace_id: str, definition: WorkflowDefinition) -> dict[str, str]:
        """Route every node whose capability resolves to exactly one published Agent.

        Strictly workspace-scoped (schema v46), no global fallback. Nodes
        without a matching published Agent keep their handler/executor path.
        """
        by_capability: dict[str, list[str]] = {}
        catalog = published_agent_definitions(self.job_db.path, workspace_id)
        for agent_id, agent_definition in catalog.items():
            by_capability.setdefault(agent_definition.capability, []).append(agent_id)
        routes: dict[str, str] = {}
        for node in definition.nodes.values():
            candidates = by_capability.get(node.capability, [])
            if len(candidates) > 1:
                raise ValueError(
                    f"Agent node {node.key!r} capability {node.capability!r} must resolve to"
                    f" exactly one published Agent; found {len(candidates)}"
                )
            if len(candidates) == 1:
                routes[node.key] = candidates[0]
        return routes

    def get_active(self, workspace_id: str, workflow_key: str) -> dict:
        revision = self.job_db.get_active_workflow_revision(workspace_id, workflow_key)
        if revision is None:
            raise ValueError(f"No active workflow revision for {workflow_key}")
        return revision

    def ensure_active_revision(self, workspace_id: str, definition: WorkflowDefinition) -> dict:
        existing = self.job_db.get_active_workflow_revision(workspace_id, definition.key)
        if existing is not None:
            return existing
        if definition.key == agent_catalog_builtin.DEMO_WORKFLOW_KEY:
            # Demo seed exception (schema v46): a workspace binding the
            # built-in demo workflow gets the factory agent templates
            # instantiated into its own catalog, seed-if-absent. Admin edits
            # inside the workspace are never overwritten.
            agent_catalog_builtin.seed_demo_workspace_agent_definitions(
                self.job_db.path, workspace_id
            )
        return self.publish_workspace_revision(workspace_id, definition)

    def reconcile_active_agent_routes(self) -> None:
        """Materialize routes for active revisions created before the Agent Catalog cutover.

        Every active revision is reconciled against its own workspace's
        published Agent catalog (workspace-scoped since schema v46), not only
        each workspace's default workflow. A revision whose Agent nodes no
        longer resolve to exactly one published Agent is skipped with a
        migration warning instead of aborting startup; a workspace with zero
        published definitions keeps its existing routes (an empty derived
        route set would prune them).
        """
        for revision in self.job_db.list_active_workflow_revisions():
            workspace_id = str(revision["workspace_id"])
            workflow_key = str(revision["workflow_key"])
            if not published_agent_definitions(self.job_db.path, workspace_id):
                logger.warning(
                    "Agent route reconcile skipped for workspace %s workflow %s (revision %s): "
                    "no published Agent Definitions in the workspace; keeping existing routes",
                    workspace_id,
                    workflow_key,
                    revision["id"],
                )
                continue
            try:
                definition = workflow_definition_from_dict(
                    json.loads(str(revision["definition_json"]))
                )
                routes = self._agent_routes(workspace_id, definition)
            except ValueError as exc:
                logger.warning(
                    "Agent route migration skipped for workspace %s workflow %s (revision %s): %s",
                    workspace_id,
                    workflow_key,
                    revision["id"],
                    exc,
                )
                continue
            self.job_db.materialize_agent_routes(
                workspace_id=workspace_id,
                workflow_key=workflow_key,
                agent_routes=routes,
            )
