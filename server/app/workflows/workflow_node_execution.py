from typing import Any

from server.app.workflows.schema import WorkflowDefinitionError, WorkflowNodeExecution


def load_node_execution(raw_node: dict[str, Any], node_key: str) -> WorkflowNodeExecution:
    raw_execution = raw_node.get("execution")
    if raw_execution is None:
        return WorkflowNodeExecution()
    if not isinstance(raw_execution, dict):
        raise WorkflowDefinitionError(f"Node {node_key}.execution must be a mapping")
    values: dict[str, str] = {}
    for field_name in ("provider", "model", "thinking", "prompt"):
        value = raw_execution.get(field_name, "")
        if not isinstance(value, str):
            raise WorkflowDefinitionError(
                f"Node {node_key}.execution.{field_name} must be a string"
            )
        values[field_name] = value
    return WorkflowNodeExecution(**values)
