from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from server.app.agent_workers import AgentWorkerRegistry
from server.app.routes.job_http import require_workflows_enabled
from server.app.services.workspace_runtime_models import workspace_runtime_models
from server.app.settings import Settings


class WorkspaceRuntimeModelsResponse(BaseModel):
    # {runtime: {provider: [models]}} across the workspace's online Workers.
    runtimes: dict[str, dict[str, list[str]]]


def create_workspace_runtime_models_router(
    registry: AgentWorkerRegistry, settings: Settings
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/workspaces/{workspace_id}/runtime-models",
        response_model=WorkspaceRuntimeModelsResponse,
    )
    def get_workspace_runtime_models(workspace_id: str) -> WorkspaceRuntimeModelsResponse:
        require_workflows_enabled(settings)
        return WorkspaceRuntimeModelsResponse(
            runtimes=workspace_runtime_models(registry, workspace_id)
        )

    return router
