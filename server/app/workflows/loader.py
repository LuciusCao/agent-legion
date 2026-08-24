from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from server.app.workflows.node_config_schema import load_node_config_schema
from server.app.workflows.schema import (
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowDefinitionError,
    WorkflowEdge,
    WorkflowIntake,
    WorkflowIntakeMode,
    WorkflowNode,
    WorkflowReduceSpec,
    WorkflowShardSpec,
    WorkflowTerminal,
)
from server.app.workflows.start_node import ensure_start_node, load_start_fields
from server.app.workflows.validator import _validate_acyclic
from server.app.workflows.workflow_node_execution import load_node_execution


def _string_list(value: Any, field_name: str, node_key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise WorkflowDefinitionError(f"{node_key}.{field_name} must be a list of strings")
    return list(value)


def _load_terminal(raw_node: dict[str, Any], node_key: str) -> WorkflowTerminal | None:
    raw_terminal = raw_node.get("terminal")
    if raw_terminal is None:
        return None
    if not isinstance(raw_terminal, dict):
        raise WorkflowDefinitionError(f"Node {node_key}.terminal must be a mapping")
    outcome = raw_terminal.get("outcome")
    if not isinstance(outcome, str) or not outcome:
        raise WorkflowDefinitionError(f"Node {node_key}.terminal.outcome is required")
    return WorkflowTerminal(outcome=outcome)


def _positive_int(value: Any, field_name: str, node_key: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise WorkflowDefinitionError(f"Node {node_key}.{field_name} must be an integer >= 1")
    return value


def _load_shard(
    raw_node: dict[str, Any], node_key: str, node_inputs: list[str]
) -> WorkflowShardSpec | None:
    raw_shard = raw_node.get("shard")
    if raw_shard is None:
        return None
    if not isinstance(raw_shard, dict):
        raise WorkflowDefinitionError(f"Node {node_key}.shard must be a mapping")
    over = raw_shard.get("over")
    count = raw_shard.get("count")
    if over is None and count is None:
        raise WorkflowDefinitionError(f"Node {node_key}.shard requires 'over' or 'count'")
    if over is not None and count is not None:
        raise WorkflowDefinitionError(
            f"Node {node_key}.shard 'over' and 'count' are mutually exclusive"
        )
    if over is not None:
        prefix = "inputs."
        if not isinstance(over, str) or not over.startswith(prefix) or not over[len(prefix) :]:
            raise WorkflowDefinitionError(
                f"Node {node_key}.shard.over must be of the form 'inputs.<name>'"
            )
        if over[len(prefix) :] not in node_inputs:
            raise WorkflowDefinitionError(
                f"Node {node_key}.shard.over {over!r} must reference a node input"
            )
    if count is not None:
        count = _positive_int(count, "shard.count", node_key)
    max_shards = _positive_int(raw_shard.get("max_shards", 1000), "shard.max_shards", node_key)
    max_concurrency = raw_shard.get("max_concurrency")
    if max_concurrency is not None:
        max_concurrency = _positive_int(max_concurrency, "shard.max_concurrency", node_key)
    return WorkflowShardSpec(
        over=over, count=count, max_concurrency=max_concurrency, max_shards=max_shards
    )


def _load_reduce(raw_node: dict[str, Any], node_key: str) -> WorkflowReduceSpec | None:
    raw_reduce = raw_node.get("reduce")
    if raw_reduce is None:
        return None
    if not isinstance(raw_reduce, dict):
        raise WorkflowDefinitionError(f"Node {node_key}.reduce must be a mapping")
    from_node = raw_reduce.get("from")
    if not isinstance(from_node, str) or not from_node:
        raise WorkflowDefinitionError(f"Node {node_key}.reduce.from is required")
    return WorkflowReduceSpec(from_node=from_node)


def _load_condition(raw_edge: dict[str, Any], edge_name: str) -> WorkflowCondition | None:
    raw_condition = raw_edge.get("when")
    if raw_condition is None:
        return None
    if not isinstance(raw_condition, dict):
        raise WorkflowDefinitionError(f"{edge_name}.when must be a mapping")
    artifact = raw_condition.get("artifact")
    path = raw_condition.get("path")
    if not isinstance(artifact, str) or not artifact:
        raise WorkflowDefinitionError(f"{edge_name}.when.artifact is required")
    if not isinstance(path, str) or not path.startswith("$."):
        raise WorkflowDefinitionError(f"{edge_name}.when.path must start with $.")
    if "equals" not in raw_condition:
        raise WorkflowDefinitionError(f"{edge_name}.when.equals is required")
    return WorkflowCondition(artifact=artifact, path=path, equals=raw_condition["equals"])


def _load_edges(
    raw: dict[str, Any], nodes: dict[str, WorkflowNode], schema_version: int
) -> list[WorkflowEdge]:
    if schema_version == 1:
        edges = [
            WorkflowEdge(source=dep, target=node.key)
            for node in nodes.values()
            for dep in node.after
        ]
        # Snapshots carry materialized edges — including loader-injected start
        # edges that ``after`` cannot express; keep any beyond the derived set.
        known = {(edge.source, edge.target) for edge in edges}
        raw_edges = raw.get("edges") or []
    else:
        raw_edges = raw.get("edges")
        if not isinstance(raw_edges, list):
            raise WorkflowDefinitionError("Workflow schema_version 2 requires edges")
        edges = []
        known = set()
    for index, raw_edge in enumerate(raw_edges):
        edge_name = f"edges[{index}]"
        if not isinstance(raw_edge, dict):
            raise WorkflowDefinitionError(f"{edge_name} must be a mapping")
        source = raw_edge.get("from")
        target = raw_edge.get("to")
        if not isinstance(source, str) or source not in nodes:
            raise WorkflowDefinitionError(f"Unknown edge source {source!r}")
        if not isinstance(target, str) or target not in nodes:
            raise WorkflowDefinitionError(f"Unknown edge target {target!r}")
        if schema_version == 1 and (source, target) in known:
            continue
        known.add((source, target))
        edges.append(
            WorkflowEdge(
                source=source,
                target=target,
                condition=_load_condition(raw_edge, edge_name),
            )
        )
    return edges


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
        if not isinstance(label, str) or not label:
            raise WorkflowDefinitionError(f"Intake mode {mode_key}.label must be a string")
        if not isinstance(input_field, str) or not input_field:
            raise WorkflowDefinitionError(f"Intake mode {mode_key}.input_field must be a string")
        modes[mode_key] = WorkflowIntakeMode(
            key=mode_key,
            label=label,
            input_field=input_field,
        )
    return WorkflowIntake(modes=modes)


def _load_nodes(
    raw_nodes: dict[str, Any],
) -> dict[str, WorkflowNode]:
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

        node_type, accepted_item_types = load_start_fields(raw_node, node_key)
        capability = raw_node.get("capability", "")
        if not isinstance(capability, str) or (not capability and node_type != "start"):
            raise WorkflowDefinitionError(f"Node {node_key} capability must be a non-empty string")

        inputs = _string_list(raw_node.get("inputs"), "inputs", node_key)
        if "resources" in raw_node:
            raise WorkflowDefinitionError(
                "Node field 'resources' was removed; configure CMS access on the "
                "node's config_schema (workspace node config + vault)."
            )
        raw_config = raw_node.get("config")
        if raw_config is None:
            raw_config = {}
        if not isinstance(raw_config, dict) or not all(isinstance(key, str) for key in raw_config):
            raise WorkflowDefinitionError(
                f"Node {node_key}.config must be a mapping with string keys"
            )
        nodes[node_key] = WorkflowNode(
            key=node_key,
            label=node_label,
            capability=capability,
            after=_string_list(raw_node.get("after"), "after", node_key),
            inputs=inputs,
            outputs=_string_list(raw_node.get("outputs"), "outputs", node_key),
            terminal=_load_terminal(raw_node, node_key),
            execution=load_node_execution(raw_node, node_key),
            config=dict(raw_config),
            config_schema=load_node_config_schema(raw_node, node_key),
            shard=_load_shard(raw_node, node_key, inputs),
            reduce=_load_reduce(raw_node, node_key),
            node_type=node_type,
            accepted_item_types=accepted_item_types,
        )

    for node in nodes.values():
        for dep in node.after:
            if dep not in nodes:
                raise WorkflowDefinitionError(f"Unknown dependency {dep!r} for node {node.key}")
        if node.shard is not None and node.reduce is not None:
            raise WorkflowDefinitionError(f"Node {node.key} cannot declare both shard and reduce")
        if node.reduce is not None:
            source = nodes.get(node.reduce.from_node)
            if source is None or source.shard is None:
                raise WorkflowDefinitionError(
                    f"Node {node.key} reduce.from {node.reduce.from_node!r} "
                    "must reference a node that declares shard"
                )

    return nodes


def workflow_definition_from_mapping(
    raw: dict[str, Any],
) -> WorkflowDefinition:
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

    schema_version = raw.get("schema_version", 1)
    if not isinstance(schema_version, int):
        raise WorkflowDefinitionError("Workflow schema_version must be an integer")

    intake = _load_intake(raw)
    nodes = _load_nodes(raw_nodes)
    edges = _load_edges(raw, nodes, schema_version)
    nodes, edges = ensure_start_node(nodes, edges)
    _validate_acyclic(nodes, edges)
    return WorkflowDefinition(
        key=key,
        label=label,
        intake=intake,
        nodes=nodes,
        edges=edges,
        schema_version=schema_version,
    )


def workflow_definition_from_dict(
    payload: dict[str, Any],
) -> WorkflowDefinition:
    if not isinstance(payload, dict):
        raise WorkflowDefinitionError("Workflow definition snapshot must be a mapping")
    raw = {
        "key": payload.get("key"),
        "label": payload.get("label"),
        "schema_version": payload.get("schema_version", 1),
        "intake": payload.get("intake", {}),
        "nodes": {},
        "edges": [],
    }
    for node_key, node in (payload.get("nodes") or {}).items():
        raw_node = dict(node)
        # Snapshots store the dataclass field name; the yaml spelling is ``type``.
        if "node_type" in raw_node:
            raw_node["type"] = raw_node.pop("node_type")
        # asdict snapshots carry every field on every node: strip the empty
        # placeholders a start node must not declare, and the default contract
        # copy on non-start nodes (only a start node may declare it).
        if raw_node.get("type") == "start":
            for placeholder in ("capability", "execution", "shard", "reduce", "terminal"):
                raw_node.pop(placeholder, None)
        else:
            raw_node.pop("accepted_item_types", None)
        terminal = raw_node.get("terminal")
        if terminal is not None:
            raw_node["terminal"] = dict(terminal)
        raw["nodes"][node_key] = raw_node
    for edge in payload.get("edges") or []:
        raw_edge = {
            "from": edge.get("source") or edge.get("from"),
            "to": edge.get("target") or edge.get("to"),
        }
        condition = edge.get("condition") or edge.get("when")
        if condition is not None:
            raw_edge["when"] = {
                "artifact": condition.get("artifact"),
                "path": condition.get("path"),
                "equals": condition.get("equals"),
            }
        raw["edges"].append(raw_edge)
    return workflow_definition_from_mapping(raw)


def load_workflow_definition(
    path: Path,
) -> WorkflowDefinition:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise WorkflowDefinitionError("Workflow definition must be a mapping")
    return workflow_definition_from_mapping(raw)
