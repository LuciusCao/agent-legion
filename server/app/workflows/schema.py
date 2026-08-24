from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class WorkflowDefinitionError(ValueError):
    """Raised when a workflow YAML file is invalid."""


#: Item types a start node may accept; also the default contract (D1).
DEFAULT_ACCEPTED_ITEM_TYPES = ("material", "ref")


@dataclass(frozen=True)
class WorkflowCondition:
    artifact: str
    path: str
    equals: Any


@dataclass(frozen=True)
class WorkflowTerminal:
    outcome: str


@dataclass(frozen=True)
class WorkflowNodeExecution:
    provider: str = ""
    model: str = ""
    thinking: str = ""
    prompt: str = ""


@dataclass(frozen=True)
class WorkflowEdge:
    source: str
    target: str
    condition: WorkflowCondition | None = None


@dataclass(frozen=True)
class WorkflowShardSpec:
    """Experimental per-node fan-out declaration; not production-supported yet."""

    over: str | None = None
    count: int | None = None
    max_concurrency: int | None = None
    max_shards: int = 1000


@dataclass(frozen=True)
class WorkflowReduceSpec:
    """Experimental fan-in declaration paired with :class:`WorkflowShardSpec`."""

    from_node: str


@dataclass(frozen=True)
class WorkflowNode:
    key: str
    label: str
    capability: str
    after: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    terminal: WorkflowTerminal | None = None
    execution: WorkflowNodeExecution = field(default_factory=WorkflowNodeExecution)
    config: dict[str, Any] = field(default_factory=dict)
    config_schema: dict[str, Any] = field(default_factory=dict)
    shard: WorkflowShardSpec | None = None
    reduce: WorkflowReduceSpec | None = None
    # ``start`` nodes carry the entry contract and never execute (EXEC-WORKFLOW-START-001).
    node_type: str = "node"
    accepted_item_types: tuple[str, ...] = DEFAULT_ACCEPTED_ITEM_TYPES


@dataclass(frozen=True)
class WorkflowIntakeMode:
    key: str
    label: str
    input_field: str


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

    @property
    def start_node(self) -> WorkflowNode | None:
        """The single ``type: start`` node; the loader injects one when absent."""
        return next((node for node in self.nodes.values() if node.node_type == "start"), None)

    @property
    def executable_nodes(self) -> dict[str, WorkflowNode]:
        """Nodes that run as job nodes — every node except the start node."""
        return {key: node for key, node in self.nodes.items() if node.node_type != "start"}
