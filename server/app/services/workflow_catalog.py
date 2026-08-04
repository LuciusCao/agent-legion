import logging
from dataclasses import asdict
from typing import Any

from server.app.services.job_errors import NotFoundError
from server.app.services.workflow_catalog_resources import (
    build_global_services,
    build_resource_providers,
)
from server.app.settings import Settings
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.registry import list_registered_workflows, load_registered_workflow

logger = logging.getLogger(__name__)


class WorkflowCatalogService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def definition(self, workflow_key: str) -> WorkflowDefinition:
        try:
            return load_registered_workflow(
                self.settings.root_dir,
                workflow_key,
                self.settings.resource_providers.providers,
            )
        except KeyError as exc:
            raise NotFoundError("Unknown workflow") from exc

    def list_workflows(self) -> list[dict[str, Any]]:
        workflows: list[dict[str, Any]] = []
        for definition in list_registered_workflows(
            self.settings.root_dir, self.settings.resource_providers.providers
        ):
            workflows.append(
                {
                    "key": definition.key,
                    "label": definition.label,
                }
            )
        return workflows

    def workflow(self, workflow_key: str) -> dict[str, Any]:
        definition = self.definition(workflow_key)
        nodes = [
            {
                "key": node.key,
                "label": node.label,
                "capability": node.capability,
                "after": node.after,
                "inputs": node.inputs,
                "outputs": node.outputs,
                "execution": asdict(node.execution),
            }
            for node in definition.nodes.values()
        ]
        edges = [
            {
                "source": edge.source,
                "target": edge.target,
                "condition": (
                    {
                        "artifact": edge.condition.artifact,
                        "path": edge.condition.path,
                        "equals": edge.condition.equals,
                    }
                    if edge.condition is not None
                    else None
                ),
            }
            for edge in definition.edges
        ]
        return {
            "key": definition.key,
            "label": definition.label,
            "intake": {
                "modes": [
                    {
                        "key": mode.key,
                        "label": mode.label,
                        "input_field": mode.input_field,
                    }
                    for mode in definition.intake.modes.values()
                ]
            },
            "nodes": nodes,
            "edges": edges,
        }

    def resource_providers(self) -> list[dict[str, Any]]:
        return build_resource_providers(self.settings)

    def global_services(self) -> dict[str, Any]:
        return build_global_services(self.settings)
