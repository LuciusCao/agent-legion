from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from server.app.agent_control.registry import AgentWorkerRegistry
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
        """Studio 节点执行 datalist 的数据源（在线 Worker 的声明聚合）。

        ``online`` 来自 30s 活性阈值（agent_control.registry
        ONLINE_THRESHOLD_SECONDS）：刚 revoked / 离线的 Worker 在活性窗口
        内仍可能短暂贡献 models——可接受边界，datalist 只是提示，claim
        匹配才是权威。通配 ``*`` 声明原样透传，字面 ``*`` 选项的过滤在
        前端 datalist 层（runtimeModelOptions.ts）。
        """
        require_workflows_enabled(settings)
        return WorkspaceRuntimeModelsResponse(
            runtimes=workspace_runtime_models(registry, workspace_id)
        )

    return router
