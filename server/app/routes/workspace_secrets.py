"""Workspace vault secrets API (spec D13, VAULT-SECRET-001).

Write-only endpoints: PUT/DELETE manage secrets, GET returns names and
metadata only. Neither plaintext nor ciphertext ever appears in a response.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.routes.job_http import raise_job_http_error, require_workflows_enabled
from server.app.services.job_errors import JobServiceError
from server.app.services.workspace_secrets import WorkspaceSecretsService
from server.app.settings import Settings


class WorkspaceSecretSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=4096)


class WorkspaceSecretMetadata(BaseModel):
    name: str
    created_at: str
    updated_at: str


class WorkspaceSecretsResponse(BaseModel):
    secrets: list[WorkspaceSecretMetadata]


class WorkspaceSecretResponse(BaseModel):
    secret: WorkspaceSecretMetadata


class WorkspaceSecretDeleteResponse(BaseModel):
    deleted: str


def create_workspace_secrets_router(
    service: WorkspaceSecretsService, settings: Settings
) -> APIRouter:
    router = APIRouter()

    @router.get("/workspaces/{workspace_id}/secrets", response_model=WorkspaceSecretsResponse)
    def list_workspace_secrets(workspace_id: str) -> WorkspaceSecretsResponse:
        require_workflows_enabled(settings)
        try:
            return WorkspaceSecretsResponse(
                secrets=[WorkspaceSecretMetadata(**entry) for entry in service.list(workspace_id)]
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.put(
        "/workspaces/{workspace_id}/secrets/{name}",
        response_model=WorkspaceSecretResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def put_workspace_secret(
        workspace_id: str, name: str, payload: WorkspaceSecretSetRequest
    ) -> WorkspaceSecretResponse:
        require_workflows_enabled(settings)
        try:
            metadata = service.set(workspace_id, name, payload.value)
            return WorkspaceSecretResponse(secret=WorkspaceSecretMetadata(**metadata))
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.delete(
        "/workspaces/{workspace_id}/secrets/{name}",
        response_model=WorkspaceSecretDeleteResponse,
        dependencies=[Depends(reject_studio_agent_scope)],
    )
    def delete_workspace_secret(workspace_id: str, name: str) -> WorkspaceSecretDeleteResponse:
        require_workflows_enabled(settings)
        try:
            service.delete(workspace_id, name)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return WorkspaceSecretDeleteResponse(deleted=name)

    return router
