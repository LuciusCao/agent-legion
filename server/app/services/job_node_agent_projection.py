"""Job detail executor projection by the job snapshot's node type (#935 R1).

Split from ``job_node_worker_projection`` (file budget). A frozen
``workspace_node_routes`` row may outlive a node the job snapshot declares
``code`` (route freeze, #440 P3), and self-contained agent nodes have no
route row at all — so whether a job node is an agent node, and which Agent
identity it shows, follows the snapshot, never the route table alone. The
decision is ``frozen_node_execution_kind``, the same one dispatch routing
(``workflow_worker.routing``) takes (#1091): job detail / MCP show the
executor the worker dispatches the node to.

#1128: a legacy agent node's route is only shown while dispatch would still
take it — the projection reuses dispatch's adjudication
(``agent_route_validation.legacy_agent_route``) and fills ``route_error``
with the same configuration error when the frozen row's Agent was archived
or republished under another capability, or the capability fallback no
longer resolves.
"""

from __future__ import annotations

from typing import Any

from server.app.executors.models import CODE_EXECUTOR_ID
from server.app.services.agent_route_validation import legacy_agent_route
from server.app.services.job_node_worker_projection import agent_route_map, claimed_worker_map
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.workflow_node_profile import (
    FROZEN_NODE_CODE,
    FROZEN_NODE_SELF_CONTAINED_AGENT,
    frozen_node_execution_kind,
)


def node_agent_ids(
    connect_source: Any, workspace_id: str, definition: WorkflowDefinition, node_keys: list[str]
) -> tuple[dict[str, str], dict[str, str]]:
    """node_key → (Agent identity, route error) of the job's agent nodes (#935 R1, #1128).

    Decided by the snapshot's frozen execution kind, exactly like dispatch
    routing (#1091), never by a route row alone: a frozen route row may
    outlive a node the snapshot declares ``code``. Self-contained nodes use
    their node key (the request row's ``agent_id``, #933); legacy agent
    nodes reuse dispatch's shared adjudication — their materialized route
    target (validated against the published catalog), else the capability
    fallback, else dispatch's configuration error as ``route_error``.
    A job node the snapshot does not declare at all keeps the route
    projection.
    """
    routes = agent_route_map(connect_source, workspace_id, workspace_id)
    ids: dict[str, str] = {}
    errors: dict[str, str] = {}
    for key in node_keys:
        node = definition.nodes.get(key)
        if node is None:
            if key in routes:
                ids[key] = routes[key]
            continue
        kind = frozen_node_execution_kind(node)
        if kind == FROZEN_NODE_CODE:
            continue
        if kind == FROZEN_NODE_SELF_CONTAINED_AGENT:
            ids[key] = key
            continue
        agent_id, error = legacy_agent_route(connect_source, workspace_id, node, routes.get(key))
        if agent_id is not None:
            ids[key] = agent_id
        if error is not None:
            errors[key] = error
    return ids, errors


def node_executor_projection(
    connect_source: Any,
    job_id: str,
    workspace_id: str,
    definition: WorkflowDefinition,
    nodes: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """node_key → execution type and identities for job detail.

    P-0.5: non-Agent nodes always run on the implicit code pool; agent-ness
    follows the job snapshot's frozen execution kind (#935 R1, #1091 — the
    dispatch decision), falling back to the route projection only for job
    nodes the snapshot does not declare. A route error has no execution type.
    """
    keys = [str(node["node_key"]) for node in nodes]
    worker_map = claimed_worker_map(connect_source, job_id)
    agent_map, route_errors = node_agent_ids(connect_source, workspace_id, definition, keys)
    projection: dict[str, dict[str, Any]] = {}
    for key in keys:
        snapshot_node = definition.nodes.get(key)
        is_agent = (
            frozen_node_execution_kind(snapshot_node) != FROZEN_NODE_CODE
            if snapshot_node is not None
            else key in agent_map
        )
        route_error = route_errors.get(key)
        projection[key] = {
            "execution_type": (
                None if route_error is not None else "agent" if is_agent else "code"
            ),
            "executor_id": None if is_agent else CODE_EXECUTOR_ID,
            "executor_kind": None if is_agent else "code",
            "worker_id": worker_map.get(key),
            "agent_id": agent_map.get(key),
            # #1128: dispatch's configuration error for a stale legacy route;
            # execution_type, executor_kind and agent_id are all None then.
            "route_error": route_error,
        }
    return projection
