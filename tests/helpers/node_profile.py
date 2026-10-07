"""Test helpers for node execution profiles (#935, #440 P3).

Since the P3 gate flip agent nodes carry their own execution profile, so a
test that needs a node-level tunable (e.g. a ``secret: true`` field) puts it
on the workflow node — republishing an Agent definition no longer reaches a
self-contained node (D4).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from server.app.workflows.definition import WorkflowDefinition


def with_node_config_schema(
    definition: WorkflowDefinition, node_key: str, config_schema: dict[str, Any]
) -> WorkflowDefinition:
    """*definition* with *node_key*'s ``config_schema`` replaced."""
    node = replace(definition.nodes[node_key], config_schema=dict(config_schema))
    return replace(definition, nodes={**definition.nodes, node_key: node})


#: write_script tunables with one secret field (the vault diversion tests).
SECRET_NODE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "api_url": {"type": "string"},
        "token": {"type": "string", "secret": True},
    },
}


def legacy_profile_variant(definition: WorkflowDefinition) -> WorkflowDefinition:
    """*definition* with every execution.runtime cleared (legacy agent nodes).

    For tests of the transitional legacy path (Agent-definition source, route
    materialization) now that the built-in demo ships self-contained nodes:
    clears the workflow top-level default and the value the loader merged
    into each node.
    """
    nodes = {
        key: replace(node, execution=replace(node.execution, runtime=""))
        for key, node in definition.nodes.items()
    }
    return replace(definition, execution=replace(definition.execution, runtime=""), nodes=nodes)
