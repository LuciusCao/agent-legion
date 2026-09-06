"""Workflow draft YAML helpers (parse/validate) and publish-gate re-export.

The publish validation itself lives in ``workflow_draft_publish_gates``
(#432 split: this file hit its budget ceiling); re-exported here so the
existing import surface stays stable.
"""

from __future__ import annotations

import yaml

from server.app.services.workflow_draft_publish_gates import validate_workflow_for_publish
from server.app.workflows.definition import (
    WorkflowDefinition,
    WorkflowDefinitionError,
    workflow_definition_from_mapping,
)

__all__ = [
    "validate_workflow_definition",
    "validate_workflow_for_publish",
    "workflow_definition_from_yaml_string",
]


def workflow_definition_from_yaml_string(raw_yaml: str) -> WorkflowDefinition:
    try:
        raw = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        raise WorkflowDefinitionError(f"Workflow definition is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkflowDefinitionError("Workflow definition must be a mapping")
    return workflow_definition_from_mapping(raw)


def validate_workflow_definition(
    raw_yaml: str,
) -> list[str]:
    try:
        workflow_definition_from_yaml_string(raw_yaml)
    except WorkflowDefinitionError as exc:
        return [str(exc)]
    return []
