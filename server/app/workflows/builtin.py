"""Built-in workflow DAG definitions (product factory defaults).

These constants replace the retired ``config/workflows/*.yaml`` files. They are
validated through the same loader used for Studio draft payloads; binding a
workspace publishes them as per-workspace DB revisions.

Keep this module dependency-minimal (workflow definition models only): scripts
import it and it must not pull in settings or database access.
"""

from __future__ import annotations

from typing import Any

from server.app.workflows.builtin_demo import DEMO_WORKFLOW_DEFINITION, DEMO_WORKFLOW_KEY
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.loader import workflow_definition_from_dict

BUILTIN_WORKFLOW_DEFINITIONS: dict[str, dict[str, Any]] = {
    DEMO_WORKFLOW_KEY: DEMO_WORKFLOW_DEFINITION,
}


def load_builtin_workflow(workflow_key: str) -> WorkflowDefinition:
    """Load and validate a built-in workflow definition; KeyError for unknown keys."""
    raw = BUILTIN_WORKFLOW_DEFINITIONS.get(workflow_key)
    if raw is None:
        raise KeyError(workflow_key)
    return workflow_definition_from_dict(raw)


def list_builtin_workflows() -> list[WorkflowDefinition]:
    """Load and validate every built-in workflow definition."""
    return [load_builtin_workflow(key) for key in BUILTIN_WORKFLOW_DEFINITIONS]
