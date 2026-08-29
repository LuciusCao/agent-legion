from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from typing import Any, cast

import yaml

from server.app.workflows.definition import WorkflowDefinition, workflow_definition_from_dict

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
                "condition": (
                    {
                        "artifact": edge.condition.artifact,
                        "path": edge.condition.path,
                        "equals": edge.condition.equals,
                    }
                    if edge.condition is not None
                    else None
                ),
            }
            for edge in definition.edges
        ],
    }


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
        # Start nodes carry the entry contract, never a capability (D1).
        raw_node: dict[str, Any] = {"label": node.label}
        if node.node_type == "start":
            raw_node["type"] = "start"
            raw_node["accepted_item_types"] = list(node.accepted_item_types)
        else:
            raw_node["capability"] = node.capability
        raw_node["after"] = node.after
        raw_node["inputs"] = node.inputs
        raw_node["outputs"] = node.outputs
        if node.terminal is not None:
            raw_node["terminal"] = {"outcome": node.terminal.outcome}
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
        payload["nodes"][key] = raw_node
    for edge in definition.edges:
        raw_edge: dict[str, Any] = {"from": edge.source, "to": edge.target}
        if edge.condition is not None:
            raw_edge["when"] = asdict(edge.condition)
        payload["edges"].append(raw_edge)
    return cast(str, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
