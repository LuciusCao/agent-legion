"""Minimal member-readable projection of shared resources used by new runs."""

from __future__ import annotations

import json
from typing import Any

from server.app.jobs import JobQueries
from server.app.services.job_errors import NotFoundError
from server.app.services.shared_resources.service import SharedResourcesService
from server.app.workflows.definition import workflow_definition_from_dict
from server.app.workflows.shared_resources import declarations_for


class RunSharedResourcesService:
    def __init__(self, job_db: JobQueries, shared_resources: SharedResourcesService) -> None:
        self._job_db = job_db
        self._shared_resources = shared_resources

    def get_confirmation(self, workspace_id: str) -> dict[str, Any]:
        if self._job_db.get_workspace(workspace_id) is None:
            raise NotFoundError("Workspace not found")
        revision = self._job_db.get_active_workflow_revision(workspace_id, workspace_id)
        if revision is None:
            raise NotFoundError("No active workflow revision")
        definition = workflow_definition_from_dict(json.loads(str(revision["definition_json"])))
        declarations = declarations_for(definition)
        nodes = [
            {"node_key": node_key, "resource_ids": resource_ids}
            for node_key, resource_ids in declarations.items()
        ]
        resources = []
        for resource_id in dict.fromkeys(
            resource_id for resource_ids in declarations.values() for resource_id in resource_ids
        ):
            try:
                resource = self._shared_resources.get(workspace_id, resource_id)
            except NotFoundError:
                continue
            selection = resource["current_selection"]
            resources.append(
                {
                    "resource_id": resource["id"],
                    "name": resource["name"],
                    "version": selection["version"] if selection is not None else None,
                    "access_status": resource["access_status"],
                }
            )
        return {"nodes": nodes, "resources": resources}
