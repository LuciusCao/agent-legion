"""Code-level access to the built-in workflow definitions (sample template).

Runtime resolution is workspace-scoped (schema v50, issue #112): services
read the workspace's ACTIVE revision via
``server.app.services.workflow_definitions``; this module remains for scripts
and tests that need the built-in sample definition without a database.
"""

from server.app.workflows.builtin import load_builtin_workflow
from server.app.workflows.definition import WorkflowDefinition


def load_registered_workflow(workflow_key: str) -> WorkflowDefinition:
    return load_builtin_workflow(workflow_key)
