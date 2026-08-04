from fastapi import APIRouter, Depends, HTTPException

from server.app.auth.dependencies import require_admin
from server.app.routes.workflow_node_file_contracts import (
    WorkflowNodeCapabilityReference,
    WorkflowNodeFileResponse,
)
from server.app.services import workflow_node_files
from server.app.settings import Settings


def create_workflow_node_files_router(settings: Settings) -> APIRouter:
    """Admin-only read-only view of repo-tracked workflow node code files.

    Global admin endpoint: node files live in the repository, not in any
    workspace, so the router must not be wrapped in require_workspace_access.
    Node code changes land through git review + CI (EXEC-CODE-001); there is
    deliberately no write endpoint.
    """
    router = APIRouter(
        prefix="/workflow-nodes/files",
        tags=["workflow-nodes"],
        dependencies=[Depends(require_admin)],
    )

    @router.get("/{file_path:path}", response_model=WorkflowNodeFileResponse)
    def read_workflow_node_file(file_path: str) -> WorkflowNodeFileResponse:
        nodes_dir = workflow_node_files.workflow_nodes_dir(settings.root_dir)
        try:
            path, content = workflow_node_files.read_node_file(nodes_dir, file_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except workflow_node_files.NodeFileError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return WorkflowNodeFileResponse(
            path=path, content=content, capabilities=_references(settings, path)
        )

    return router


def _references(settings: Settings, path: str) -> list[WorkflowNodeCapabilityReference]:
    return [
        WorkflowNodeCapabilityReference(**reference)
        for reference in workflow_node_files.referencing_capabilities(
            settings.executor_definitions, path
        )
    ]
