"""Frozen Agent-route retention for revision publication (#936).

Revision publication no longer derives or writes route targets. Historical
``workspace_node_routes`` rows remain available to legacy job snapshots; the
publish write path only prunes rows whose node was deleted or turned ``code``.
"""

from __future__ import annotations

from server.app.workflows.definition import WorkflowDefinition


def agent_route_nodes(definition: WorkflowDefinition) -> frozenset[str]:
    """Node keys whose historical agent route rows survive this revision."""
    return frozenset(key for key, node in definition.nodes.items() if node.node_type == "agent")
