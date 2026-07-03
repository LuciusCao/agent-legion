import json
from typing import Any

from server.app.workflows.schema import WorkflowCondition


def format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def format_condition(condition: WorkflowCondition | None) -> str:
    if condition is None:
        return ""
    return f"{condition.path} == {format_value(condition.equals)}"


def condition_identity(condition: WorkflowCondition | None) -> str:
    if condition is None:
        return ""
    return json.dumps(
        {"artifact": condition.artifact, "path": condition.path, "equals": condition.equals},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
