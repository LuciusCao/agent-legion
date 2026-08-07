from server.app.workflows.builtin import list_builtin_workflows, load_builtin_workflow
from server.app.workflows.definition import WorkflowDefinition


def load_registered_workflow(workflow_key: str) -> WorkflowDefinition:
    return load_builtin_workflow(workflow_key)


def list_registered_workflows() -> list[WorkflowDefinition]:
    return list_builtin_workflows()
