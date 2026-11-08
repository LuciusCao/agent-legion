from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from typing import Any

from server.app.workflows.definition import WorkflowDefinition, workflow_definition_from_dict
from server.app.workflows.revision_echo import definition_to_yaml as definition_to_yaml
from server.app.workflows.start_text_input import text_input_payload
from server.app.workflows.workflow_node_execution import node_execution_payload

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
                "text_input": text_input_payload(node.text_input),
                "after": node.after,
                "inputs": node.inputs,
                "outputs": node.outputs,
                "execution": node_execution_payload(node.execution),
                "skill": asdict(node.skill) if node.skill is not None else None,
                # Omitted when undeclared (empty) so payload/yaml stay clean.
                **({"tools": list(node.tools)} if node.tools else {}),
                # #933: self-contained profile labels, omitted when undeclared.
                **({"requires_labels": dict(node.requires_labels)} if node.requires_labels else {}),
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
