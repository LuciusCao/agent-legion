from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class WorkflowDefinitionError(ValueError):
    """Raised when a workflow YAML file is invalid."""


@dataclass(frozen=True)
class WorkflowNode:
    key: str
    label: str
    capability: str
    after: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WorkflowIntakeMode:
    key: str
    label: str
    input_field: str
    resource: str = ""


@dataclass(frozen=True)
class WorkflowIntake:
    modes: dict[str, WorkflowIntakeMode] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowDefinition:
    key: str
    label: str
    intake: WorkflowIntake
    nodes: dict[str, WorkflowNode]

    @property
    def terminal_nodes(self) -> list[str]:
        referenced = {dep for node in self.nodes.values() for dep in node.after}
        return [key for key in self.nodes if key not in referenced]


def _string_list(value: Any, field_name: str, node_key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise WorkflowDefinitionError(f"{node_key}.{field_name} must be a list of strings")
    return list(value)


def _validate_acyclic(nodes: dict[str, WorkflowNode]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_key: str) -> None:
        if node_key in visiting:
            raise WorkflowDefinitionError(f"Workflow contains a cycle at node {node_key}")
        if node_key in visited:
            return
        visiting.add(node_key)
        for dep in nodes[node_key].after:
            visit(dep)
        visiting.remove(node_key)
        visited.add(node_key)

    for key in nodes:
        visit(key)


def _load_intake(raw: dict[str, Any]) -> WorkflowIntake:
    raw_intake = raw.get("intake", {})
    if raw_intake is None:
        raw_intake = {}
    if not isinstance(raw_intake, dict):
        raise WorkflowDefinitionError("Workflow intake must be a mapping")
    raw_modes = raw_intake.get("modes", {})
    if raw_modes is None:
        raw_modes = {}
    if not isinstance(raw_modes, dict):
        raise WorkflowDefinitionError("Workflow intake.modes must be a mapping")

    modes: dict[str, WorkflowIntakeMode] = {}
    for mode_key, raw_mode in raw_modes.items():
        if not isinstance(mode_key, str) or not mode_key:
            raise WorkflowDefinitionError("Intake mode keys must be non-empty strings")
        if not isinstance(raw_mode, dict):
            raise WorkflowDefinitionError(f"Intake mode {mode_key} must be a mapping")
        label = raw_mode.get("label", mode_key)
        input_field = raw_mode.get("input_field", mode_key)
        resource = raw_mode.get("resource", "")
        if not isinstance(label, str) or not label:
            raise WorkflowDefinitionError(f"Intake mode {mode_key}.label must be a string")
        if not isinstance(input_field, str) or not input_field:
            raise WorkflowDefinitionError(f"Intake mode {mode_key}.input_field must be a string")
        if not isinstance(resource, str):
            raise WorkflowDefinitionError(f"Intake mode {mode_key}.resource must be a string")
        modes[mode_key] = WorkflowIntakeMode(
            key=mode_key,
            label=label,
            input_field=input_field,
            resource=resource,
        )
    return WorkflowIntake(modes=modes)


def load_workflow_definition(path: Path) -> WorkflowDefinition:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise WorkflowDefinitionError("Workflow definition must be a mapping")

    key = raw.get("key")
    label = raw.get("label")
    raw_nodes = raw.get("nodes")

    if not isinstance(key, str) or not key:
        raise WorkflowDefinitionError("Workflow key is required")
    if not isinstance(label, str) or not label:
        raise WorkflowDefinitionError("Workflow label is required")
    if not isinstance(raw_nodes, dict) or not raw_nodes:
        raise WorkflowDefinitionError("Workflow nodes are required")

    if "concurrency" in raw:
        raise WorkflowDefinitionError(
            "Workflow field 'concurrency' was removed; configure Executor limits at Workspace level."
        )

    intake = _load_intake(raw)

    nodes: dict[str, WorkflowNode] = {}
    for node_key, raw_node in raw_nodes.items():
        if not isinstance(node_key, str) or not node_key:
            raise WorkflowDefinitionError("Node keys must be non-empty strings")
        if not isinstance(raw_node, dict):
            raise WorkflowDefinitionError(f"Node {node_key} must be a mapping")

        if "runner" in raw_node:
            raise WorkflowDefinitionError(
                "Node field 'runner' was removed; bind a compatible Executor in Workspace settings."
            )
        if "agent" in raw_node:
            raise WorkflowDefinitionError(
                "Node field 'agent' was removed; invocation details belong to Executor capabilities."
            )

        node_label = raw_node.get("label", node_key)
        if not isinstance(node_label, str) or not node_label:
            raise WorkflowDefinitionError(f"Node {node_key} label must be a non-empty string")

        capability = raw_node.get("capability", "")
        if not isinstance(capability, str) or not capability:
            raise WorkflowDefinitionError(f"Node {node_key} capability must be a non-empty string")

        nodes[node_key] = WorkflowNode(
            key=node_key,
            label=node_label,
            capability=capability,
            after=_string_list(raw_node.get("after"), "after", node_key),
            inputs=_string_list(raw_node.get("inputs"), "inputs", node_key),
            outputs=_string_list(raw_node.get("outputs"), "outputs", node_key),
        )

    for node in nodes.values():
        for dep in node.after:
            if dep not in nodes:
                raise WorkflowDefinitionError(f"Unknown dependency {dep!r} for node {node.key}")

    _validate_acyclic(nodes)
    return WorkflowDefinition(
        key=key,
        label=label,
        intake=intake,
        nodes=nodes,
    )
