from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class WorkflowDefinitionError(ValueError):
    """Raised when a workflow YAML file is invalid."""


@dataclass(frozen=True)
class WorkflowCondition:
    artifact: str
    path: str
    equals: Any


@dataclass(frozen=True)
class WorkflowTerminal:
    outcome: str


@dataclass(frozen=True)
class WorkflowEdge:
    source: str
    target: str
    condition: WorkflowCondition | None = None


@dataclass(frozen=True)
class WorkflowNode:
    key: str
    label: str
    capability: str
    after: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    terminal: WorkflowTerminal | None = None


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
    edges: list[WorkflowEdge] = field(default_factory=list)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version == 1 and not self.edges:
            object.__setattr__(
                self,
                "edges",
                [
                    WorkflowEdge(source=dep, target=node.key)
                    for node in self.nodes.values()
                    for dep in node.after
                ],
            )

    @property
    def terminal_nodes(self) -> list[str]:
        referenced = {edge.source for edge in self.edges}
        return [key for key in self.nodes if key not in referenced]
