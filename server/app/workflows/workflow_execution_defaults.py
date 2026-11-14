"""Legacy workflow-level execution defaults, materialized into node profiles.

Persisted revisions, intake-frozen job snapshots and historical draft mappings
may still contain the top-level block. The explicit legacy loader fills empty
fields on Agent nodes and non-runtime fields on pre-explicit-type aliases,
preserving node overrides and legacy nodes whose runtime was never declared.
Explicit non-Agent nodes are left untouched; strict authoring rejects the block
on every new write (#805).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from server.app.workflows.schema import (
    WorkflowDefinitionError,
    WorkflowNode,
    WorkflowNodeExecution,
)
from server.app.workflows.workflow_node_profile import validate_agent_runtime


def load_workflow_execution(raw: dict[str, Any]) -> WorkflowNodeExecution:
    """Read legacy defaults before materializing them into node profiles."""
    raw_execution = raw.get("execution")
    if raw_execution is None:
        return WorkflowNodeExecution()
    if not isinstance(raw_execution, dict):
        raise WorkflowDefinitionError("Workflow execution must be a mapping")
    values: dict[str, str] = {}
    for field_name in ("provider", "model", "thinking", "runtime"):
        value = raw_execution.get(field_name, "")
        if not isinstance(value, str):
            raise WorkflowDefinitionError(f"Workflow execution.{field_name} must be a string")
        values[field_name] = value
    validate_agent_runtime(values["runtime"], "Workflow execution.runtime")
    if raw_execution.get("prompt"):
        raise WorkflowDefinitionError("Workflow execution.prompt is not allowed (node-level only)")
    if raw_execution.get("prompt_mode"):
        raise WorkflowDefinitionError(
            "Workflow execution.prompt_mode is not allowed (node-level only)"
        )
    return WorkflowNodeExecution(**values)


def merge_execution_defaults(
    node: WorkflowNode, defaults: WorkflowNodeExecution, *, legacy_untyped: bool = False
) -> WorkflowNode:
    """Fill Agent fields, plus non-runtime fields on pre-explicit-type nodes."""
    if node.node_type != "agent" and not legacy_untyped:
        return node
    execution = node.execution
    merged = WorkflowNodeExecution(
        provider=execution.provider or defaults.provider,
        model=execution.model or defaults.model,
        thinking=execution.thinking or defaults.thinking,
        prompt=execution.prompt,
        prompt_mode=execution.prompt_mode,
        runtime=execution.runtime or (defaults.runtime if node.node_type == "agent" else ""),
    )
    if merged == execution:
        return node
    return replace(node, execution=merged)


def apply_execution_defaults(
    nodes: dict[str, WorkflowNode],
    defaults: WorkflowNodeExecution,
    *,
    legacy_untyped_keys: frozenset[str] = frozenset(),
) -> dict[str, WorkflowNode]:
    if not (defaults.provider or defaults.model or defaults.thinking or defaults.runtime):
        return nodes
    return {
        key: merge_execution_defaults(node, defaults, legacy_untyped=key in legacy_untyped_keys)
        for key, node in nodes.items()
    }


def apply_workflow_execution(
    raw: dict[str, Any], nodes: dict[str, WorkflowNode]
) -> dict[str, WorkflowNode]:
    """Materialize stored defaults without guessing types for explicit code nodes."""
    raw_nodes = raw.get("nodes") or {}
    legacy_untyped_keys = frozenset(
        key for key, raw_node in raw_nodes.items() if raw_node.get("type") in (None, "node")
    )
    return apply_execution_defaults(
        nodes,
        load_workflow_execution(raw),
        legacy_untyped_keys=legacy_untyped_keys,
    )
