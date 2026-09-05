from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from typing import Any, cast

import yaml

from server.app.workflows.definition import WorkflowDefinition, workflow_definition_from_dict
from server.app.workflows.schema import WorkflowNode
from server.app.workflows.workflow_node_skill import apply_skill_echo

logger = logging.getLogger(__name__)


def serialize_definition(definition: WorkflowDefinition) -> str:
    payload = asdict(definition)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def definition_hash(definition_json: str) -> str:
    return hashlib.sha256(definition_json.encode("utf-8")).hexdigest()


def definition_from_job_snapshot(job: dict) -> WorkflowDefinition | None:
    """Intake-frozen definition; ``None`` = no snapshot (legacy) or corrupt.

    Known risk: a corrupt snapshot also falls back to the *current* definition
    at every caller (kept for read-path compatibility); the warning is the
    tripwire for that case.
    """
    raw = job.get("workflow_definition_snapshot_json") or ""
    if not raw:
        return None
    try:
        payload = json.loads(str(raw))
        return workflow_definition_from_dict(payload)
    except Exception:
        # #204 broad-except audit: 损坏快照的读路径降级（docstring 契约：
        # None = 无快照（legacy）或损坏，调用方一律回退当前 definition）。
        # 失败语义是「数据态而非编程错误」：json.JSONDecodeError 之外，
        # workflow_definition_from_dict 及其全部校验链（snapshot_shape、
        # schema、loader、start_node、intake、node_config_schema 等）只抛
        # WorkflowDefinitionError（isinstance 校验风格，#243 P1 特意保证
        # 不漏 AttributeError/TypeError 杀死 worker 启动）；但快照是
        # 不可信持久化输入，未来任何链条新增异常类型都必须落进「损坏」
        # 语义而非让读路径崩溃——这正是本臂保持宽的原因。warning +
        # exc_info 是 docstring 所说的 tripwire：损坏快照静默回退当前
        # definition 的行为由此对操作者可见。
        logger.warning("Corrupt workflow snapshot for job %s", job.get("id"), exc_info=True)
        return None


def workflow_definition_to_response_payload(definition: WorkflowDefinition) -> dict[str, Any]:
    return {
        "key": definition.key,
        "label": definition.label,
        "intake": {
            "modes": [
                {
                    "key": mode.key,
                    "label": mode.label,
                    "input_field": mode.input_field,
                }
                for mode in definition.intake.modes.values()
            ]
        },
        "nodes": [
            {
                "key": node.key,
                "label": node.label,
                "capability": node.capability,
                "node_type": node.node_type,
                "accepted_item_types": list(node.accepted_item_types),
                "after": node.after,
                "inputs": node.inputs,
                "outputs": node.outputs,
                "execution": asdict(node.execution),
                "skill": asdict(node.skill) if node.skill is not None else None,
                # Omitted when undeclared (empty) so payload/yaml stay clean.
                **({"tools": list(node.tools)} if node.tools else {}),
                "config": node.config,
                "terminal": (
                    {"outcome": node.terminal.outcome} if node.terminal is not None else None
                ),
            }
            for node in definition.nodes.values()
        ],
        "edges": [
            {
                "source": edge.source,
                "target": edge.target,
                "condition": asdict(edge.condition) if edge.condition is not None else None,
            }
            for edge in definition.edges
        ],
    }


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
        execution = {
            key: value
            for key, value in asdict(node.execution).items()
            if value and (key == "prompt" or value != top_execution.get(key))
        }
        if execution:
            raw_node["execution"] = execution
        if node.config:
            raw_node["config"] = node.config
        if node.config_schema:
            raw_node["config_schema"] = node.config_schema
        if node.tools:
            raw_node["tools"] = list(node.tools)
        apply_skill_echo(raw_node, node)
        payload["nodes"][key] = raw_node
    for edge in definition.edges:
        raw_edge: dict[str, Any] = {"from": edge.source, "to": edge.target}
        if edge.condition is not None:
            raw_edge["when"] = asdict(edge.condition)
        payload["edges"].append(raw_edge)
    return cast(str, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
