import logging
from typing import Any

from server.app.services.job_errors import NotFoundError
from server.app.services.workflow_revision_format import workflow_definition_to_response_payload
from server.app.settings import Settings
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.registry import list_registered_workflows, load_registered_workflow

logger = logging.getLogger(__name__)


class WorkflowCatalogService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def definition(self, workflow_key: str) -> WorkflowDefinition:
        try:
            return load_registered_workflow(workflow_key)
        except KeyError as exc:
            raise NotFoundError("Unknown workflow") from exc

    def list_workflows(self) -> list[dict[str, Any]]:
        workflows: list[dict[str, Any]] = []
        for definition in list_registered_workflows():
            workflows.append(
                {
                    "key": definition.key,
                    "label": definition.label,
                }
            )
        return workflows

    def workflow(self, workflow_key: str) -> dict[str, Any]:
        return workflow_definition_to_response_payload(self.definition(workflow_key))
