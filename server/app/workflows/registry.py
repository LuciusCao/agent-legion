"""Code-level access to the built-in workflow definitions (seed source).

Runtime reads (routes, workspace binding, worker scan list) go through the
DB-backed catalog in ``server.app.services.workflow_catalog_store``
(DB-WORKFLOW-CATALOG-001); this module remains for scripts and tests that need
the built-in definitions without a database.
"""

from server.app.workflows.builtin import list_builtin_workflows, load_builtin_workflow
from server.app.workflows.definition import WorkflowDefinition


def load_registered_workflow(workflow_key: str) -> WorkflowDefinition:
    return load_builtin_workflow(workflow_key)


def list_registered_workflows() -> list[WorkflowDefinition]:
    return list_builtin_workflows()
