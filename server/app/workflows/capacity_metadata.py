from __future__ import annotations

from server.app.workflows.approval_node import APPROVAL_NODE_TYPE
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.revision_format import definition_from_job_snapshot


def node_capacity_requirements(definition: WorkflowDefinition) -> dict[str, bool]:
    """Static capacity metadata for rows created from this definition."""
    return {
        key: node.node_type != APPROVAL_NODE_TYPE
        for key, node in definition.executable_nodes.items()
    }


def node_capacity_requirements_from_snapshot(snapshot_json: str) -> dict[str, bool]:
    definition = definition_from_job_snapshot({"workflow_definition_snapshot_json": snapshot_json})
    return node_capacity_requirements(definition) if definition is not None else {}
