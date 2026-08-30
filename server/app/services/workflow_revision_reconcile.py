"""Shared Agent-route pipeline: publish-time derivation + startup reconcile (#287).

Why a separate module: the publish path and the startup reconcile share the
route-derivation rule (one published Agent per capability) but answer to
different failure contracts — publish *fails fast* on an ambiguous or empty
mapping, while the reconcile runs once at Host startup over every active
revision and must stay best-effort (warn and skip; boot never aborts on one
workspace's stale catalog). Keeping the two apart is what keeps the publish
transaction free of log-and-continue logic. ``WorkflowRevisionService``
delegates here so its public surface is unchanged.
"""

from __future__ import annotations

import json
import logging

from server.app.services.agent_service import published_agent_definitions
from server.app.workflows.definition import WorkflowDefinition, workflow_definition_from_dict

logger = logging.getLogger(__name__)


def derive_agent_routes(
    job_db, workspace_id: str, definition: WorkflowDefinition
) -> dict[str, str]:
    """Route every node whose capability resolves to exactly one published Agent.

    Strictly workspace-scoped (schema v46), no global fallback. Nodes
    without a matching published Agent keep their handler/executor path.
    """
    by_capability: dict[str, list[str]] = {}
    catalog = published_agent_definitions(job_db, workspace_id)
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


def reconcile_active_agent_routes(job_db) -> None:
    """Materialize routes for active revisions created before the Agent Catalog cutover.

    Every active revision is reconciled against its own workspace's
    published Agent catalog (workspace-scoped since schema v46), not only
    each workspace's default workflow. A revision whose Agent nodes no
    longer resolve to exactly one published Agent is skipped with a
    migration warning instead of aborting startup; a workspace with zero
    published definitions keeps its existing routes (an empty derived
    route set would prune them).
    """
    for revision in job_db.list_active_workflow_revisions():
        workspace_id = str(revision["workspace_id"])
        workflow_key = str(revision["workflow_key"])
        if not published_agent_definitions(job_db, workspace_id):
            logger.warning(
                "Agent route reconcile skipped for workspace %s workflow %s (revision %s): "
                "no published Agent Definitions in the workspace; keeping existing routes",
                workspace_id,
                workflow_key,
                revision["id"],
            )
            continue
        try:
            definition = workflow_definition_from_dict(json.loads(str(revision["definition_json"])))
            routes = derive_agent_routes(job_db, workspace_id, definition)
        except ValueError as exc:
            logger.warning(
                "Agent route migration skipped for workspace %s workflow %s (revision %s): %s",
                workspace_id,
                workflow_key,
                revision["id"],
                exc,
            )
            continue
        job_db.materialize_agent_routes(
            workspace_id=workspace_id,
            workflow_key=workflow_key,
            agent_routes=routes,
        )
