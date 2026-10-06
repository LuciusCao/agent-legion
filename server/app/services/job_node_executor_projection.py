"""Executor-kind projection of job-detail nodes (#284, #933).

Split from ``job_node_worker_projection`` (file budget): classifies a node
from the job's frozen definition, falling back to the current Agent route
only for snapshot-less legacy jobs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.executors.models import CODE_EXECUTOR_ID
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.workflow_node_profile import is_self_contained_agent_node


def node_executor_projection(
    definition: WorkflowDefinition | None,
    node: Mapping[str, Any],
    agent_map: Mapping[str, str],
    worker_map: Mapping[str, str],
) -> dict[str, Any]:
    """Executor / Agent / Worker fields of one job-detail node.

    P-0.5: non-agent nodes always run on the implicit code pool. When the
    job has a frozen definition carrying the node, the frozen node decides
    entirely (#284, #933 — routes are current-state and may since have been
    added or removed for the same node key): code nodes never read a route,
    a self-contained agent node's identity is its node key (what its
    requests carry), and only a legacy agent node looks up its Agent route.
    *definition* is None for snapshot-less legacy jobs, which (like a node
    missing from the frozen definition) fall back to the current route.
    """
    node_key = str(node["node_key"])
    frozen = definition.nodes.get(node_key) if definition is not None else None
    if frozen is None:
        agent_id = agent_map.get(node_key)
        is_agent = agent_id is not None
    elif frozen.node_type != "agent":
        agent_id, is_agent = None, False
    else:
        is_agent = True
        agent_id = node_key if is_self_contained_agent_node(frozen) else agent_map.get(node_key)
    return {
        "executor_id": None if is_agent else CODE_EXECUTOR_ID,
        "executor_kind": None if is_agent else "code",
        "worker_id": worker_map.get(node_key),
        "agent_id": agent_id,
    }
