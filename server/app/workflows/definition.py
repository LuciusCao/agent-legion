import yaml

from server.app.workflows.loader import (
    load_workflow_definition,
    workflow_definition_from_dict,
    workflow_definition_from_mapping,
)
from server.app.workflows.schema import (
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowDefinitionError,
    WorkflowEdge,
    WorkflowIntake,
    WorkflowIntakeMode,
    WorkflowNode,
    WorkflowNodeSkill,
    WorkflowTerminal,
)
from server.app.workflows.validator import _validate_acyclic
from server.app.workflows.workflow_node_profile import reject_workflow_execution

__all__ = [
    "WorkflowCondition",
    "WorkflowDefinition",
    "WorkflowDefinitionError",
    "WorkflowEdge",
    "WorkflowIntake",
    "WorkflowIntakeMode",
    "WorkflowNode",
    "WorkflowNodeSkill",
    "WorkflowTerminal",
    "_validate_acyclic",
    "load_workflow_definition",
    "reject_retired_workflow_execution_yaml",
    "workflow_definition_from_dict",
    "workflow_definition_from_mapping",
]


def reject_retired_workflow_execution_yaml(raw_yaml: str) -> None:
    """Reject a parseable authoring YAML retired block; leave broken YAML to the caller."""
    try:
        raw = yaml.safe_load(raw_yaml)
    except (yaml.YAMLError, RecursionError):
        return
    if isinstance(raw, dict):
        reject_workflow_execution(raw)
