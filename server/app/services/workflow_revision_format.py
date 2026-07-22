from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any, cast

import yaml

from server.app.workflows.definition import WorkflowDefinition, workflow_definition_from_dict


def serialize_definition(definition: WorkflowDefinition) -> str:
    payload = asdict(definition)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def definition_hash(definition_json: str) -> str:
    return hashlib.sha256(definition_json.encode("utf-8")).hexdigest()


def definition_from_job_snapshot(job: dict) -> WorkflowDefinition | None:
    raw = job.get("workflow_definition_snapshot_json") or ""
    if not raw:
        return None
    try:
        payload = json.loads(str(raw))
        return workflow_definition_from_dict(payload)
    except Exception:
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
                    "resource": mode.resource,
                }
                for mode in definition.intake.modes.values()
            ]
        },
        "nodes": [
            {
                "key": node.key,
                "label": node.label,
                "capability": node.capability,
                "max_concurrency": node.max_concurrency,
                "after": node.after,
                "inputs": node.inputs,
                "outputs": node.outputs,
                "execution": asdict(node.execution),
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
                    **({"resource": mode.resource} if mode.resource else {}),
                }
                for key, mode in definition.intake.modes.items()
            }
        },
        "nodes": {},
        "edges": [],
    }
    for key, node in definition.nodes.items():
        raw_node: dict[str, Any] = {
            "label": node.label,
            "capability": node.capability,
            "after": node.after,
            "inputs": node.inputs,
            "outputs": node.outputs,
        }
        if node.max_concurrency is not None:
            raw_node["max_concurrency"] = node.max_concurrency
        if node.terminal is not None:
            raw_node["terminal"] = {"outcome": node.terminal.outcome}
        execution = {key: value for key, value in asdict(node.execution).items() if value}
        if execution:
            raw_node["execution"] = execution
        payload["nodes"][key] = raw_node
    for edge in definition.edges:
        raw_edge: dict[str, Any] = {"from": edge.source, "to": edge.target}
        if edge.condition is not None:
            raw_edge["when"] = asdict(edge.condition)
        payload["edges"].append(raw_edge)
    return cast(str, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
