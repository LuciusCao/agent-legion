from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from server.app.services.job_errors import InvalidOperationError
from server.app.workflows.definition import WorkflowDefinition


def validate_workspace_node_limits(
    *,
    workflow: WorkflowDefinition | None,
    node_limits: Sequence[Mapping[str, Any]],
    agent_capabilities: Collection[str] = (),
    code_capacity: int,
) -> None:
    """Validate per-node concurrency limits (P-0.5: the only node-level knob).

    Node limits cap concurrency inside the implicit code pool, so a limit may
    never exceed the instance code_capacity. Agent-routed nodes run on Agent
    workers, not the code pool, and cannot carry a node limit.
    """
    seen_limits: set[tuple[str, str]] = set()
    for node_limit in node_limits:
        key = (str(node_limit["workflow_key"]), str(node_limit["node_key"]))
        if key in seen_limits:
            raise InvalidOperationError(f"Duplicate Node limit {key[0]}.{key[1]}")
        seen_limits.add(key)
        if int(node_limit["concurrency_limit"]) > code_capacity:
            raise InvalidOperationError(
                f"Node limit for {key[0]}.{key[1]} exceeds the code pool capacity {code_capacity}"
            )
        # A registered workflow before its first publish has no catalog
        # definition: node existence/routing checks wait for publish-time
        # validation (validate_workflow_for_publish).
        if workflow is None:
            continue
        if key[0] != workflow.key or key[1] not in workflow.nodes:
            raise InvalidOperationError(f"Unknown Workflow Node {key[0]}.{key[1]}")
        if workflow.nodes[key[1]].capability in agent_capabilities:
            raise InvalidOperationError(
                f"Agent-routed Node {key[0]}.{key[1]} cannot have a Node limit"
            )
