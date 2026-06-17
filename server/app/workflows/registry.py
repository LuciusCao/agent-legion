import contextlib
from pathlib import Path

from server.app.workflows.definition import WorkflowDefinition, load_workflow_definition

WORKFLOW_FILES = {
    "question_content": "question_content.yaml",
    "reading_analysis": "reading_analysis.yaml",
    "question_comprehension_info": "question_comprehension_info.yaml",
}


def load_registered_workflow(root_dir: Path, workflow_key: str) -> WorkflowDefinition:
    filename = WORKFLOW_FILES.get(workflow_key)
    if filename is None:
        raise KeyError(workflow_key)
    return load_workflow_definition(root_dir / "config" / "workflows" / filename)


def list_registered_workflows(root_dir: Path) -> list[WorkflowDefinition]:
    workflows: list[WorkflowDefinition] = []
    for key in WORKFLOW_FILES:
        with contextlib.suppress(KeyError, FileNotFoundError):
            workflows.append(load_registered_workflow(root_dir, key))
    return workflows
