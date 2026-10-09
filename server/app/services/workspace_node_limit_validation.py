from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from server.app.services.job_errors import InvalidOperationError
from server.app.workflows.definition import WorkflowDefinition


def validate_workspace_node_limits(
    *,
    workflow: WorkflowDefinition | None,
    node_limits: Sequence[Mapping[str, Any]],
    code_capacity: int,
) -> None:
    """Validate per-node concurrency limits (P-0.5: the only node-level knob).

    Node limits cap concurrency inside the implicit code pool, so a limit may
    never exceed the instance code_capacity. ``type: agent`` nodes run on
    Agent workers, not the code pool, and cannot carry a node limit (the
    explicit node type decides, #284). In pure-remote mode (#389,
    ``code_capacity == 0``) there is no local pool to bound against — the
    node limit is still enforced for remote code claims: claim_evaluate
    counts the node's active executor_leases and skips the request while the
    limit is saturated (issue #1149), so limits are accepted without the
    ceiling check.
    """
    seen_limits: set[str] = set()
    for node_limit in node_limits:
        node_key = str(node_limit["node_key"])
        if node_key in seen_limits:
            raise InvalidOperationError(f"Duplicate Node limit {node_key}")
        seen_limits.add(node_key)
        if code_capacity > 0 and int(node_limit["concurrency_limit"]) > code_capacity:
            raise InvalidOperationError(
                f"Node limit for {node_key} exceeds the code pool capacity {code_capacity}"
            )
        # A registered workflow before its first publish has no catalog
        # definition: node existence/routing checks wait for publish-time
        # validation (validate_workflow_for_publish).
        if workflow is None:
            continue
        if node_key not in workflow.nodes:
            raise InvalidOperationError(f"Unknown Workflow Node {workflow.key}.{node_key}")
        # Explicit node type decides (#284): a code node may share its
        # capability with a published Agent and still carry a node limit.
        if workflow.nodes[node_key].node_type == "agent":
            raise InvalidOperationError(
                f"Agent-routed Node {workflow.key}.{node_key} cannot have a Node limit"
            )
