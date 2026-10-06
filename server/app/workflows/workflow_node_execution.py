from dataclasses import asdict
from typing import Any

from server.app.workflows.schema import WorkflowDefinitionError, WorkflowNodeExecution

#: #513：自定义提示词的拼接模式。append（默认）= 追加在默认指令之后，
#: overwrite = 覆写默认指令。平台信封两种模式下都不可覆盖。
PROMPT_MODES = ("append", "overwrite")

_STRING_FIELDS = ("provider", "model", "thinking", "prompt", "prompt_mode", "runtime")


def load_node_execution(raw_node: dict[str, Any], node_key: str) -> WorkflowNodeExecution:
    raw_execution = raw_node.get("execution")
    if raw_execution is None:
        return WorkflowNodeExecution()
    if not isinstance(raw_execution, dict):
        raise WorkflowDefinitionError(f"Node {node_key}.execution must be a mapping")
    values: dict[str, str] = {}
    for field_name in _STRING_FIELDS:
        value = raw_execution.get(field_name, "")
        if not isinstance(value, str):
            raise WorkflowDefinitionError(
                f"Node {node_key}.execution.{field_name} must be a string"
            )
        values[field_name] = value
    mode = values.get("prompt_mode", "")
    if mode and mode not in PROMPT_MODES:
        raise WorkflowDefinitionError(
            f"Node {node_key}.execution.prompt_mode must be one of "
            f"{', '.join(PROMPT_MODES)} (empty = append)"
        )
    # #933: the runtime's allowed set and the agent-only rule are checked by
    # ``workflow_node_profile`` once the node type is known.
    return WorkflowNodeExecution(**values)


def node_execution_payload(execution: WorkflowNodeExecution) -> dict[str, str]:
    """The ``node_execution`` mapping manifests and API payloads carry.

    ``runtime`` (#933) is dropped when empty so every legacy-sourced
    manifest stays byte-identical to the pre-P2 shape; a self-contained
    node's runtime rides along (the manifest's top-level ``runtime`` is the
    authoritative copy).
    """
    payload = asdict(execution)
    if not payload.get("runtime"):
        payload.pop("runtime", None)
    return payload
