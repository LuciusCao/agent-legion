"""Shared Agent-route derivation for revision publication (#287, #284).

Why a separate module: the publish pipeline (workflow_revision_pipeline.py)
keeps no service state, and the route-derivation rule it needs is a pure
function of the workspace's published Agent catalog and the definition.
The startup reconcile this module used to host retired with explicit node
types (#284 phase 2): Agent publish/archive never rewrites routes — they
change only at revision publication.
"""

from __future__ import annotations

from server.app.services.agent_service import published_agent_definitions
from server.app.workflows.definition import WorkflowDefinition


def derive_agent_routes(
    job_db, workspace_id: str, definition: WorkflowDefinition
) -> dict[str, str]:
    """Route every ``type: agent`` node to its one published Agent.

    Strictly workspace-scoped (schema v46), no global fallback. ``code``
    nodes never get a route row: they join the implicit code pool and the
    read side treats a missing row as code (P-0.5). Publish fails fast on
    an ambiguous mapping — a capability with more than one published Agent
    is a catalog error, never a silent pick.
    """
    by_capability: dict[str, list[str]] = {}
    catalog = published_agent_definitions(job_db, workspace_id)
    for agent_id, agent_definition in catalog.items():
        by_capability.setdefault(agent_definition.capability, []).append(agent_id)
    routes: dict[str, str] = {}
    for node in definition.nodes.values():
        if node.node_type != "agent":
            continue
        candidates = by_capability.get(node.capability, [])
        if len(candidates) > 1:
            raise ValueError(
                f"Agent node {node.key!r} capability {node.capability!r} must resolve to"
                f" exactly one published Agent; found {len(candidates)}"
            )
        if len(candidates) == 1:
            routes[node.key] = candidates[0]
    return routes
