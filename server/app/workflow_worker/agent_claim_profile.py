"""Execution-profile resolution for one agent claim (#932, #933).

Split from ``agent_claim`` (file budget). Two sources:

- ``node`` — a self-contained agent node: the profile is projected from the
  job snapshot's node, no catalog read. A quality-replay Agent version pin
  cannot apply (there is no Agent version), so it fails closed; a node
  profile pin (#1079, D6) must match the snapshot node's profile hash;
- ``agent_definition`` — the routed published Agent (or the pinned version,
  schema v29) through the profile facade.

Returns the profile, or the configuration-failure message for the node.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from server.app.services.agent_node_profile_catalog import resolve_dispatch_agent_profile
from server.app.services.agent_node_profile_types import (
    PROFILE_SOURCE_NODE,
    AgentNodeProfile,
    profile_from_node,
)
from server.app.services.node_profile_pins import node_profile_pin_error

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread
    from server.app.workflows.definition import WorkflowNode


def resolve_claim_profile(
    worker: WorkflowWorkerThread,
    workspace_id: str,
    agent_id: str,
    node: WorkflowNode,
    pin: Mapping[str, Any] | None,
    profile_source: str,
    profile_pin: Mapping[str, Any] | None = None,
) -> AgentNodeProfile | str:
    """The node's dispatch profile, or a configuration-failure message."""
    if profile_pin is not None:
        # #1079（D6）：回放副本的执行档案已移植进副本快照；claim 只复核
        # 快照节点与冻结 pin 一致（不一致即节点失败，fail closed）。
        if profile_source != PROFILE_SOURCE_NODE:
            return (
                f"node {node.key} carries a replay profile pin but its snapshot node is not"
                " a self-contained agent node"
            )
        mismatch = node_profile_pin_error(node, profile_pin)
        if mismatch is not None:
            return mismatch
    if profile_source == PROFILE_SOURCE_NODE:
        if pin is not None:
            return (
                f"node {node.key} is a self-contained agent node; pinned Agent version"
                " replay applies only to Agent-definition nodes"
            )
        return profile_from_node(node)
    try:
        profile = resolve_dispatch_agent_profile(worker.job_db, workspace_id, agent_id, pin)
    except ValueError as exc:
        return str(exc)
    if profile is None or profile.legacy_ref is None:  # resolve_node_route validated this
        return (
            f"Agent {agent_id!r} has no published definition in workspace {workspace_id!r};"
            " agent definitions are workspace-scoped (schema v46) — create one in"
            " Studio (Agent 管理) for this workspace"
        )
    return profile
