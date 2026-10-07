"""Job detail executor projection by the job snapshot's node type (#935 R1).

Split from ``job_node_worker_projection`` (file budget). A frozen
``workspace_node_routes`` row may outlive a node the job snapshot declares
``code`` (route freeze, #440 P3), and self-contained agent nodes have no
route row at all — so whether a job node is an agent node, and which Agent
identity it shows, follows the snapshot, never the route table alone.
"""

from __future__ import annotations

from typing import Any

from server.app.executors.models import CODE_EXECUTOR_ID
from server.app.services.job_node_worker_projection import agent_route_map, claimed_worker_map
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.workflow_node_profile import is_self_contained_agent_node


def node_agent_ids(
    connect_source: Any, workspace_id: str, definition: WorkflowDefinition, node_keys: list[str]
) -> dict[str, str]:
    """node_key → Agent identity of the job's agent nodes (#935 R1).

    Decided by the snapshot's node type, never by a route row alone: a
    frozen route row may outlive a node the snapshot declares ``code``.
    Self-contained nodes use their node key (the request row's ``agent_id``,
    #933); legacy agent nodes use their materialized route target. A job
    node the snapshot does not declare at all keeps the route projection.
    """
    routes = agent_route_map(connect_source, workspace_id, workspace_id)
    ids: dict[str, str] = {}
    for key in node_keys:
        node = definition.nodes.get(key)
        if node is None:
            if key in routes:
                ids[key] = routes[key]
        elif node.node_type != "agent":
            continue
        elif is_self_contained_agent_node(node):
            ids[key] = key
        elif key in routes:
            ids[key] = routes[key]
    return ids


def node_executor_projection(
    connect_source: Any,
    job_id: str,
    workspace_id: str,
    definition: WorkflowDefinition,
    nodes: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """node_key → executor_id / executor_kind / worker_id / agent_id for job detail.

    P-0.5: non-Agent nodes always run on the implicit code pool; agent-ness
    follows the job snapshot's node type (#935 R1), falling back to the
    route projection only for job nodes the snapshot does not declare.
    """
    keys = [str(node["node_key"]) for node in nodes]
    worker_map = claimed_worker_map(connect_source, job_id)
    agent_map = node_agent_ids(connect_source, workspace_id, definition, keys)
    projection: dict[str, dict[str, Any]] = {}
    for key in keys:
        snapshot_node = definition.nodes.get(key)
        is_agent = (
            snapshot_node.node_type == "agent" if snapshot_node is not None else key in agent_map
        )
        projection[key] = {
            "executor_id": None if is_agent else CODE_EXECUTOR_ID,
            "executor_kind": None if is_agent else "code",
            "worker_id": worker_map.get(key),
            "agent_id": agent_map.get(key),
        }
    return projection
