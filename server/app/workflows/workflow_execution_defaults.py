"""Workflow 顶层 execution 默认（schema v63）：校验与向节点的合并。

workspace 级 Agent 默认（default_agent_*）退役后，顶层 ``execution`` 块是
workflow 作用域的默认来源：loader 在定义加载时把它合并进每个非 start
节点的 execution（节点值优先），dispatch 读到的节点值即有效值。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from server.app.workflows.schema import (
    WorkflowDefinitionError,
    WorkflowNode,
    WorkflowNodeExecution,
)


def load_workflow_execution(raw: dict[str, Any]) -> WorkflowNodeExecution:
    """Optional top-level ``execution`` defaults (provider/model/thinking).

    Same validation as the node-level block, minus ``prompt`` — a default
    prompt makes no sense across nodes. Snapshots (asdict round-trips) carry
    an empty ``prompt`` key, which is tolerated; a non-empty one is rejected.
    """
    raw_execution = raw.get("execution")
    if raw_execution is None:
        return WorkflowNodeExecution()
    if not isinstance(raw_execution, dict):
        raise WorkflowDefinitionError("Workflow execution must be a mapping")
    values: dict[str, str] = {}
    for field_name in ("provider", "model", "thinking"):
        value = raw_execution.get(field_name, "")
        if not isinstance(value, str):
            raise WorkflowDefinitionError(f"Workflow execution.{field_name} must be a string")
        values[field_name] = value
    if raw_execution.get("prompt"):
        raise WorkflowDefinitionError("Workflow execution.prompt is not allowed (node-level only)")
    return WorkflowNodeExecution(**values)


def merge_execution_defaults(node: WorkflowNode, defaults: WorkflowNodeExecution) -> WorkflowNode:
    """Fill a node's empty execution fields from the workflow-level defaults.

    Start nodes are exempt (they never execute); code-routed nodes simply
    never read the execution block, so merging into every non-start node
    keeps the loader routing-agnostic — the merge happens at definition load
    time where Agent bindings are not yet known.
    """
    if node.node_type == "start":
        return node
    execution = node.execution
    merged = WorkflowNodeExecution(
        provider=execution.provider or defaults.provider,
        model=execution.model or defaults.model,
        thinking=execution.thinking or defaults.thinking,
        prompt=execution.prompt,
    )
    if merged == execution:
        return node
    return replace(node, execution=merged)


def apply_execution_defaults(
    nodes: dict[str, WorkflowNode], defaults: WorkflowNodeExecution
) -> dict[str, WorkflowNode]:
    """Merge the top-level defaults into every node; no-op when undeclared."""
    if not (defaults.provider or defaults.model or defaults.thinking):
        return nodes
    return {key: merge_execution_defaults(node, defaults) for key, node in nodes.items()}
