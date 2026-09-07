from fastapi import APIRouter

from server.app.routes.agent_runtimes_contracts import AgentRuntimesResponse
from server.app.services.agent_runtime_catalog import AgentRuntimeCatalogService


def create_agent_runtimes_router(
    catalog: AgentRuntimeCatalogService | None = None,
) -> APIRouter:
    """Per-runtime tool catalog for Studio's dynamic tool picker (#476).

    Read-only projection of the runtime adapter declarations — no DB access,
    so the service is stateless and lazily constructed.
    """
    router = APIRouter()

    @router.get(
        "/agent-runtimes",
        response_model=AgentRuntimesResponse,
        response_model_exclude_none=True,
    )
    def get_agent_runtimes() -> AgentRuntimesResponse:
        service = catalog or AgentRuntimeCatalogService()
        return AgentRuntimesResponse(**service.tool_catalog())

    return router
