import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from server.app.auth.dependencies import require_user
from server.app.jobs import JobQueries
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.workflow_node_code_contracts import (
    WorkflowNodeCodeArchiveResponse,
    WorkflowNodeCodeDraftRequest,
    WorkflowNodeCodeResponse,
    WorkflowNodeCodeRollbackRequest,
    WorkflowNodeCodeVersionResponse,
    WorkflowNodeCodeVersionsResponse,
    WorkflowNodeCodeVersionSummary,
)
from server.app.services import workflow_node_files
from server.app.services.job_errors import JobServiceError
from server.app.services.node_codes import NodeCodeService
from server.app.settings import Settings
from server.app.workflows.definition import workflow_definition_from_dict


def create_workflow_node_codes_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    """Workspace-scoped custom node code editing and publish flow (EXEC-CODE-002).

    Mounted through ``secured()``: workspace members (viewer read, editor
    write) reach these endpoints — custom code is user data, not a repo file.
    """
    router = APIRouter()

    def _service() -> NodeCodeService:
        return NodeCodeService(
            job_db.path, settings.executor_runtime.workflows.custom_nodes_enabled
        )

    def _capability(workspace_id: str, workflow_key: str, node_key: str) -> str:
        revision = job_db.get_active_workflow_revision(workspace_id, workflow_key)
        if revision is None:
            raise HTTPException(status_code=404, detail="No active workflow revision")
        definition = workflow_definition_from_dict(json.loads(str(revision["definition_json"])))
        node = definition.nodes.get(node_key)
        if node is None:
            raise HTTPException(status_code=404, detail=f"Unknown workflow node: {node_key}")
        return node.capability

    def _read_builtin_code(capability: str) -> tuple[str, str]:
        path = workflow_node_files.builtin_code_path(settings.executor_definitions, capability)
        if path is None:
            raise HTTPException(
                status_code=404,
                detail=f"No builtin code file for capability: {capability}",
            )
        nodes_dir = workflow_node_files.workflow_nodes_dir(settings.root_dir)
        try:
            return workflow_node_files.read_node_file(nodes_dir, path)
        except (FileNotFoundError, workflow_node_files.NodeFileError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code",
        response_model=WorkflowNodeCodeResponse,
    )
    def get_node_code(
        workspace_id: str, workflow_key: str, node_key: str
    ) -> WorkflowNodeCodeResponse:
        capability = _capability(workspace_id, workflow_key, node_key)
        try:
            versions = _service().list_versions(workspace_id, workflow_key, node_key)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        published = next((row for row in versions if row["status"] == "published"), None)
        has_draft = any(row["status"] == "draft" for row in versions)
        if published is not None:
            return WorkflowNodeCodeResponse(
                origin="custom",
                code=str(published["code"]),
                version=int(published["version"]),
                has_draft=has_draft,
            )
        path, content = _read_builtin_code(capability)
        return WorkflowNodeCodeResponse(
            origin="builtin", code=content, path=path, has_draft=has_draft
        )

    @router.put(
        "/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code",
        response_model=WorkflowNodeCodeVersionResponse,
    )
    def save_node_code_draft(
        workspace_id: str,
        workflow_key: str,
        node_key: str,
        request: WorkflowNodeCodeDraftRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> WorkflowNodeCodeVersionResponse:
        _capability(workspace_id, workflow_key, node_key)
        try:
            row = _service().save_draft(
                workspace_id,
                workflow_key,
                node_key,
                request.code,
                f"user:{user['id']}",
                request.change_note,
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return WorkflowNodeCodeVersionResponse(**row)

    @router.post(
        "/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code/publish",
        response_model=WorkflowNodeCodeVersionResponse,
    )
    def publish_node_code(
        workspace_id: str, workflow_key: str, node_key: str
    ) -> WorkflowNodeCodeVersionResponse:
        _capability(workspace_id, workflow_key, node_key)
        try:
            row = _service().publish(workspace_id, workflow_key, node_key)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return WorkflowNodeCodeVersionResponse(**row)

    @router.get(
        "/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code/versions",
        response_model=WorkflowNodeCodeVersionsResponse,
    )
    def list_node_code_versions(
        workspace_id: str, workflow_key: str, node_key: str
    ) -> WorkflowNodeCodeVersionsResponse:
        _capability(workspace_id, workflow_key, node_key)
        try:
            rows = _service().list_versions(workspace_id, workflow_key, node_key)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return WorkflowNodeCodeVersionsResponse(
            versions=[WorkflowNodeCodeVersionSummary(**row) for row in rows]
        )

    @router.post(
        "/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code/rollback",
        response_model=WorkflowNodeCodeVersionResponse,
    )
    def rollback_node_code(
        workspace_id: str,
        workflow_key: str,
        node_key: str,
        request: WorkflowNodeCodeRollbackRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> WorkflowNodeCodeVersionResponse:
        _capability(workspace_id, workflow_key, node_key)
        try:
            row = _service().rollback(
                workspace_id,
                workflow_key,
                node_key,
                request.version,
                f"user:{user['id']}",
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return WorkflowNodeCodeVersionResponse(**row)

    @router.delete(
        "/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code",
        response_model=WorkflowNodeCodeArchiveResponse,
    )
    def archive_node_code(
        workspace_id: str, workflow_key: str, node_key: str
    ) -> WorkflowNodeCodeArchiveResponse:
        _capability(workspace_id, workflow_key, node_key)
        try:
            archived = _service().archive_all(workspace_id, workflow_key, node_key)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return WorkflowNodeCodeArchiveResponse(archived=archived)

    return router
