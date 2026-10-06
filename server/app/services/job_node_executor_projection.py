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

    The projection mirrors what dispatch actually does
    (``workflow_worker/routing.resolve_node_route``):

    - a frozen self-contained agent node (#933) dispatches from the job
      snapshot, so its identity is the node key whatever routes exist now;
    - every other node follows the current Agent route like dispatch does
      — including a frozen ``code`` node that a later revision routed to an
      Agent. Dispatch not honouring the frozen type is a pre-existing gap
      tracked in #1091; once dispatch follows the frozen type, switch this
      back to frozen-type-first (code never reads the route);
    - a frozen legacy agent node with no route still is an agent node
      (dispatch falls back to its capability, #1039).

    *definition* is None for snapshot-less legacy jobs (current route only).
    """
    node_key = str(node["node_key"])
    frozen = definition.nodes.get(node_key) if definition is not None else None
    if frozen is not None and is_self_contained_agent_node(frozen):
        agent_id: str | None = node_key
        is_agent = True
    else:
        agent_id = agent_map.get(node_key)
        is_agent = agent_id is not None or (frozen is not None and frozen.node_type == "agent")
    return {
        "executor_id": None if is_agent else CODE_EXECUTOR_ID,
        "executor_kind": None if is_agent else "code",
        "worker_id": worker_map.get(node_key),
        "agent_id": agent_id,
    }
