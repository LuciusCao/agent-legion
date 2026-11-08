from __future__ import annotations

from dataclasses import asdict
from typing import Any, cast

import yaml

from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.schema import WorkflowNode
from server.app.workflows.start_text_input import text_input_payload
from server.app.workflows.workflow_node_skill import apply_skill_echo


def apply_shard_echo(raw_node: dict[str, Any], node: WorkflowNode) -> None:
    """Echo the fan-out declaration; omitted when unset. Only non-default
    keys are written (the loader re-applies max_shards 1000 and the None
    optionals), so the echo re-parses to an equal spec (#458).
    """
    if node.shard is None:
        return
    shard: dict[str, Any] = {}
    if node.shard.over is not None:
        shard["over"] = node.shard.over
    if node.shard.count is not None:
        shard["count"] = node.shard.count
    if node.shard.max_concurrency is not None:
        shard["max_concurrency"] = node.shard.max_concurrency
    if node.shard.max_shards != 1000:
        shard["max_shards"] = node.shard.max_shards
    raw_node["shard"] = shard


def definition_to_yaml(definition: WorkflowDefinition) -> str:
    payload: dict[str, Any] = {
        "key": definition.key,
        "label": definition.label,
        "schema_version": 2,
        "intake": {
            "modes": {
                key: {
                    "label": mode.label,
                    "input_field": mode.input_field,
                }
                for key, mode in definition.intake.modes.items()
            }
        },
    }
    # prompt 不是顶层默认键（loader 拒绝），asdict 带出的空 prompt 过滤掉。
    top_execution = {k: v for k, v in asdict(definition.execution).items() if k != "prompt" and v}
    # Top-level defaults precede ``nodes:`` so the echo reads as defaults,
    # not as another per-node block.
    if top_execution:
        payload["execution"] = top_execution
    payload["nodes"] = {}
    payload["edges"] = []
    for key, node in definition.nodes.items():
        raw_node: dict[str, Any] = {"label": node.label}
        # The explicit type must round-trip — dropping it would normalize an
        # Agent node back to code on the next load. start/approval declare no
        # capability; start carries the entry contract instead (D1).
        raw_node["type"] = node.node_type
        if node.node_type == "start":
            raw_node["accepted_item_types"] = list(node.accepted_item_types)
            if node.text_input is not None:
                raw_node["text_input"] = text_input_payload(node.text_input)
        if node.node_type not in ("start", "approval"):
            raw_node["capability"] = node.capability
        raw_node["after"] = node.after
        raw_node["inputs"] = node.inputs
        raw_node["outputs"] = node.outputs
        if node.terminal is not None:
            raw_node["terminal"] = {"outcome": node.terminal.outcome}
        # #458: the fan-out/fan-in declarations must echo or the studio's
        # initial YAML (built from the active revision) shows a ghost change
        # against its own baseline — and publishing that echo would silently
        # drop them.
        apply_shard_echo(raw_node, node)
        if node.reduce is not None:
            raw_node["reduce"] = {"from": node.reduce.from_node}
        # The loader bakes the top-level defaults into every non-start node;
        # subtract them back out key by key so the echo only carries genuine
        # node-level overrides — otherwise a later edit of the top-level
        # defaults would silently lose to the baked per-node values.
        # prompt/prompt_mode 是节点级专属键（顶层块 loader 拒绝），非空即
        # 节点自身声明，随 echo 保留。
        execution = {
            key: value
            for key, value in asdict(node.execution).items()
            if value and (key in ("prompt", "prompt_mode") or value != top_execution.get(key))
        }
        if execution:
            raw_node["execution"] = execution
        if node.config:
            raw_node["config"] = node.config
        if node.config_schema:
            raw_node["config_schema"] = node.config_schema
        if node.tools:
            raw_node["tools"] = list(node.tools)
        # #933: self-contained agent profile labels (runtime echoes with
        # the execution block above).
        if node.requires_labels:
            raw_node["requires_labels"] = dict(node.requires_labels)
        apply_skill_echo(raw_node, node)
        payload["nodes"][key] = raw_node
    for edge in definition.edges:
        raw_edge: dict[str, Any] = {"from": edge.source, "to": edge.target}
        if edge.condition is not None:
            raw_edge["when"] = asdict(edge.condition)
        payload["edges"].append(raw_edge)
    return cast(str, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
