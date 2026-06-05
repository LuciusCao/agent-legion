from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

RunnerKind = Literal["local", "agent"]


class PipelineDefinitionError(ValueError):
    """Raised when a pipeline YAML file is invalid."""


@dataclass(frozen=True)
class PipelineConcurrency:
    local: int = 1
    agent: int = 1


@dataclass(frozen=True)
class PipelineNode:
    key: str
    runner: RunnerKind
    after: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PipelineDefinition:
    key: str
    label: str
    concurrency: PipelineConcurrency
    nodes: dict[str, PipelineNode]

    @property
    def terminal_nodes(self) -> list[str]:
        referenced = {dep for node in self.nodes.values() for dep in node.after}
        return [key for key in self.nodes if key not in referenced]


def _string_list(value: Any, field_name: str, node_key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PipelineDefinitionError(f"{node_key}.{field_name} must be a list of strings")
    return list(value)


def _positive_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise PipelineDefinitionError(f"concurrency.{field_name} must be a positive integer")
    return value


def _validate_acyclic(nodes: dict[str, PipelineNode]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_key: str) -> None:
        if node_key in visiting:
            raise PipelineDefinitionError(f"Pipeline contains a cycle at node {node_key}")
        if node_key in visited:
            return
        visiting.add(node_key)
        for dep in nodes[node_key].after:
            visit(dep)
        visiting.remove(node_key)
        visited.add(node_key)

    for key in nodes:
        visit(key)


def load_pipeline_definition(path: Path) -> PipelineDefinition:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise PipelineDefinitionError("Pipeline definition must be a mapping")

    key = raw.get("key")
    label = raw.get("label")
    raw_nodes = raw.get("nodes")

    if not isinstance(key, str) or not key:
        raise PipelineDefinitionError("Pipeline key is required")
    if not isinstance(label, str) or not label:
        raise PipelineDefinitionError("Pipeline label is required")
    if not isinstance(raw_nodes, dict) or not raw_nodes:
        raise PipelineDefinitionError("Pipeline nodes are required")

    raw_concurrency = raw.get("concurrency", {})
    if raw_concurrency is None:
        raw_concurrency = {}
    if not isinstance(raw_concurrency, dict):
        raise PipelineDefinitionError("Pipeline concurrency must be a mapping")

    concurrency = PipelineConcurrency(
        local=_positive_int(raw_concurrency.get("local", 1), "local"),
        agent=_positive_int(raw_concurrency.get("agent", 1), "agent"),
    )

    nodes: dict[str, PipelineNode] = {}
    for node_key, raw_node in raw_nodes.items():
        if not isinstance(node_key, str) or not node_key:
            raise PipelineDefinitionError("Node keys must be non-empty strings")
        if not isinstance(raw_node, dict):
            raise PipelineDefinitionError(f"Node {node_key} must be a mapping")

        runner = raw_node.get("runner", "local")
        if runner not in {"local", "agent"}:
            raise PipelineDefinitionError(f"Node {node_key} has invalid runner {runner!r}")

        nodes[node_key] = PipelineNode(
            key=node_key,
            runner=runner,
            after=_string_list(raw_node.get("after"), "after", node_key),
            inputs=_string_list(raw_node.get("inputs"), "inputs", node_key),
            outputs=_string_list(raw_node.get("outputs"), "outputs", node_key),
        )

    for node in nodes.values():
        for dep in node.after:
            if dep not in nodes:
                raise PipelineDefinitionError(f"Unknown dependency {dep!r} for node {node.key}")

    _validate_acyclic(nodes)
    return PipelineDefinition(key=key, label=label, concurrency=concurrency, nodes=nodes)
