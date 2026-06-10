from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

RunnerKind = Literal["local", "agent"]
AgentEngine = Literal["pi"]

PI_TOOLS = {"read", "write", "edit", "bash", "grep", "find", "ls"}


class PipelineDefinitionError(ValueError):
    """Raised when a pipeline YAML file is invalid."""


@dataclass(frozen=True)
class PipelineConcurrency:
    local: int = 1
    agent: int = 1


@dataclass(frozen=True)
class PipelineAgent:
    engine: AgentEngine
    skill: str
    tools: list[str] = field(default_factory=lambda: ["read", "write", "bash"])


@dataclass(frozen=True)
class PipelineNode:
    key: str
    label: str
    runner: RunnerKind
    after: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    agent: PipelineAgent | None = None


@dataclass(frozen=True)
class PipelineIntakeMode:
    key: str
    label: str
    input_field: str
    resource: str = ""


@dataclass(frozen=True)
class PipelineIntake:
    modes: dict[str, PipelineIntakeMode] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineDefinition:
    key: str
    label: str
    concurrency: PipelineConcurrency
    intake: PipelineIntake
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


def _load_intake(raw: dict[str, Any]) -> PipelineIntake:
    raw_intake = raw.get("intake", {})
    if raw_intake is None:
        raw_intake = {}
    if not isinstance(raw_intake, dict):
        raise PipelineDefinitionError("Pipeline intake must be a mapping")
    raw_modes = raw_intake.get("modes", {})
    if raw_modes is None:
        raw_modes = {}
    if not isinstance(raw_modes, dict):
        raise PipelineDefinitionError("Pipeline intake.modes must be a mapping")

    modes: dict[str, PipelineIntakeMode] = {}
    for mode_key, raw_mode in raw_modes.items():
        if not isinstance(mode_key, str) or not mode_key:
            raise PipelineDefinitionError("Intake mode keys must be non-empty strings")
        if not isinstance(raw_mode, dict):
            raise PipelineDefinitionError(f"Intake mode {mode_key} must be a mapping")
        label = raw_mode.get("label", mode_key)
        input_field = raw_mode.get("input_field", mode_key)
        resource = raw_mode.get("resource", "")
        if not isinstance(label, str) or not label:
            raise PipelineDefinitionError(f"Intake mode {mode_key}.label must be a string")
        if not isinstance(input_field, str) or not input_field:
            raise PipelineDefinitionError(f"Intake mode {mode_key}.input_field must be a string")
        if not isinstance(resource, str):
            raise PipelineDefinitionError(f"Intake mode {mode_key}.resource must be a string")
        modes[mode_key] = PipelineIntakeMode(
            key=mode_key,
            label=label,
            input_field=input_field,
            resource=resource,
        )
    return PipelineIntake(modes=modes)


def _load_agent(
    raw_node: dict[str, Any], node_key: str, runner: RunnerKind
) -> PipelineAgent | None:
    raw_agent = raw_node.get("agent")
    if raw_agent is None:
        return None
    if not isinstance(raw_agent, dict):
        raise PipelineDefinitionError(f"Node {node_key} agent block must be a mapping")
    if runner == "local":
        raise PipelineDefinitionError(f"Node {node_key} has agent block but runner is local")

    engine = raw_agent.get("engine")
    if engine != "pi":
        raise PipelineDefinitionError(f"Node {node_key} agent.engine must be 'pi', got {engine!r}")

    skill = raw_agent.get("skill", "")
    if not isinstance(skill, str) or not skill:
        raise PipelineDefinitionError(f"Node {node_key} agent.skill must be a non-empty string")
    if skill.startswith("/"):
        raise PipelineDefinitionError(
            f"Node {node_key} agent.skill must be a relative path, got {skill!r}"
        )
    if ".." in skill.split("/"):
        raise PipelineDefinitionError(
            f"Node {node_key} agent.skill must not contain '..' components, got {skill!r}"
        )

    raw_tools = raw_agent.get("tools", ["read", "write", "bash"])
    if not isinstance(raw_tools, list) or not raw_tools:
        raise PipelineDefinitionError(f"Node {node_key} agent.tools must be a non-empty list")
    tools: list[str] = []
    for tool in raw_tools:
        if not isinstance(tool, str) or tool not in PI_TOOLS:
            raise PipelineDefinitionError(
                f"Node {node_key} agent.tools contains invalid tool {tool!r}"
            )
        tools.append(tool)

    return PipelineAgent(engine="pi", skill=skill, tools=tools)


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
    intake = _load_intake(raw)

    nodes: dict[str, PipelineNode] = {}
    for node_key, raw_node in raw_nodes.items():
        if not isinstance(node_key, str) or not node_key:
            raise PipelineDefinitionError("Node keys must be non-empty strings")
        if not isinstance(raw_node, dict):
            raise PipelineDefinitionError(f"Node {node_key} must be a mapping")

        runner = raw_node.get("runner", "local")
        if runner not in {"local", "agent"}:
            raise PipelineDefinitionError(f"Node {node_key} has invalid runner {runner!r}")

        node_label = raw_node.get("label", node_key)
        if not isinstance(node_label, str) or not node_label:
            raise PipelineDefinitionError(f"Node {node_key} label must be a non-empty string")

        agent = _load_agent(raw_node, node_key, runner)

        nodes[node_key] = PipelineNode(
            key=node_key,
            label=node_label,
            runner=runner,
            after=_string_list(raw_node.get("after"), "after", node_key),
            inputs=_string_list(raw_node.get("inputs"), "inputs", node_key),
            outputs=_string_list(raw_node.get("outputs"), "outputs", node_key),
            agent=agent,
        )

    for node in nodes.values():
        for dep in node.after:
            if dep not in nodes:
                raise PipelineDefinitionError(f"Unknown dependency {dep!r} for node {node.key}")

    _validate_acyclic(nodes)
    return PipelineDefinition(
        key=key,
        label=label,
        concurrency=concurrency,
        intake=intake,
        nodes=nodes,
    )
