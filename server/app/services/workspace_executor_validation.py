from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from server.app.executors.config import ExecutorConfig
from server.app.services.job_errors import InvalidOperationError
from server.app.workflows.definition import WorkflowDefinition


def validate_workspace_executor_configuration(
    *,
    workflow: WorkflowDefinition,
    executor_definitions: Mapping[str, ExecutorConfig],
    allocations: Sequence[Mapping[str, Any]],
    bindings: Sequence[Mapping[str, Any]],
    node_limits: Sequence[Mapping[str, Any]],
    agent_capabilities: Collection[str] = (),
) -> None:
    allocation_by_id: dict[str, Mapping[str, Any]] = {}
    for allocation in allocations:
        executor_id = str(allocation["executor_id"])
        if executor_id in allocation_by_id:
            raise InvalidOperationError(f"Duplicate Executor allocation {executor_id}")
        definition = executor_definitions.get(executor_id)
        if definition is None:
            raise InvalidOperationError(f"Unknown Executor {executor_id}")
        limit = int(allocation["concurrency_limit"])
        if limit > definition.global_capacity:
            raise InvalidOperationError(
                f"Workspace limit {limit} exceeds {executor_id} global capacity "
                f"{definition.global_capacity}"
            )
        allocation_by_id[executor_id] = allocation

    binding_by_node: dict[tuple[str, str], Mapping[str, Any]] = {}
    for binding in bindings:
        workflow_key = str(binding["workflow_key"])
        node_key = str(binding["node_key"])
        key = (workflow_key, node_key)
        if key in binding_by_node:
            raise InvalidOperationError(f"Duplicate Node binding {workflow_key}.{node_key}")
        if workflow_key != workflow.key or node_key not in workflow.nodes:
            raise InvalidOperationError(f"Unknown Workflow Node {workflow_key}.{node_key}")
        capability = workflow.nodes[node_key].capability
        if capability in agent_capabilities:
            raise InvalidOperationError(
                f"Agent Node {workflow_key}.{node_key} is routed by Agent ID, not Executor binding"
            )
        executor_id = str(binding["executor_id"])
        if executor_id not in allocation_by_id:
            raise InvalidOperationError(
                f"Executor {executor_id} is not allocated to this Workspace"
            )
        executor = executor_definitions[executor_id]
        if capability not in executor.capabilities:
            raise InvalidOperationError(
                f"Executor {executor_id} does not support capability {capability} "
                f"for {workflow_key}.{node_key}"
            )
        binding_by_node[key] = binding

    seen_limits: set[tuple[str, str]] = set()
    for node_limit in node_limits:
        key = (str(node_limit["workflow_key"]), str(node_limit["node_key"]))
        if key in seen_limits:
            raise InvalidOperationError(f"Duplicate Node limit {key[0]}.{key[1]}")
        bound = binding_by_node.get(key)
        if bound is None:
            raise InvalidOperationError(f"Node limit requires binding for {key[0]}.{key[1]}")
        executor_id = str(bound["executor_id"])
        if executor_definitions[executor_id].kind != "code":
            raise InvalidOperationError(
                f"Agent-bound Node {key[0]}.{key[1]} cannot have a Node limit"
            )
        workspace_limit = int(allocation_by_id[executor_id]["concurrency_limit"])
        if int(node_limit["concurrency_limit"]) > workspace_limit:
            raise InvalidOperationError(
                f"Node limit for {key[0]}.{key[1]} exceeds Workspace allocation for {executor_id}"
            )
        seen_limits.add(key)
