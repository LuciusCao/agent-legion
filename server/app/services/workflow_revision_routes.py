"""Shared Agent-route derivation for revision publication (#287, #284).

Why a separate module: the publish pipeline (workflow_revision_pipeline.py)
keeps no service state, and the route-derivation rule it needs is a pure
function of the workspace's published Agent catalog and the definition.
The startup reconcile this module used to host retired with explicit node
types (#284 phase 2): Agent publish/archive never rewrites routes — they
change only at revision publication.
"""

from __future__ import annotations

from server.app.services.agent_node_profile import build_capability_index, legacy_agent_candidates
from server.app.services.agent_node_profile_catalog import legacy_agent_catalog
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.workflow_node_profile import is_self_contained_agent_node


def has_legacy_agent_nodes(definition: WorkflowDefinition) -> bool:
    """True when some ``type: agent`` node still resolves its profile by capability."""
    return any(
        node.node_type == "agent" and not is_self_contained_agent_node(node)
        for node in definition.nodes.values()
    )


def derive_agent_routes(
    job_db, workspace_id: str, definition: WorkflowDefinition
) -> dict[str, str]:
    """Route every legacy ``type: agent`` node to its one published Agent.

    Self-contained agent nodes (``execution.runtime`` declared, #933) get no
    route: dispatch reads their profile straight from the job snapshot.

    Strictly workspace-scoped (schema v46), no global fallback. ``code``
    nodes never get a route row: they join the implicit code pool and the
    read side treats a missing row as code (P-0.5). Publish fails fast on
    an ambiguous mapping — a capability with more than one published Agent
    is a catalog error, never a silent pick.
    """
    catalog = legacy_agent_catalog(job_db, workspace_id)
    index = build_capability_index(catalog)
    routes: dict[str, str] = {}
    for node in definition.nodes.values():
        if node.node_type != "agent":
            continue
        # #933: self-contained nodes dispatch from their own profile and
        # never materialize a route (EXEC-AGENT-PROFILE-001).
        if is_self_contained_agent_node(node):
            continue
        # #932: routes materialize only legacy-sourced profiles (the target
        # is a published Agent id); ambiguity stays a publish error here.
        candidates = legacy_agent_candidates(node, catalog, index=index)
        if len(candidates) > 1:
            raise ValueError(
                f"Agent node {node.key!r} capability {node.capability!r} must resolve to"
                f" exactly one published Agent; found {len(candidates)}"
            )
        if len(candidates) == 1:
            routes[node.key] = candidates[0]
    return routes
