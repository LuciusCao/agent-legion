"""Admin routes for the Studio chat ACP agent registry (phase 3 chunk 4).

The registry document lives in ``global_settings`` under the
``studio_agents`` key (see server.app.studio_chat.registry for why it is not
folded into the monolithic instance settings document). Admin-only: this is
where agent command lines enter the system.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import require_admin
from server.app.jobs import JobQueries
from server.app.routes.studio_agents_admin_contracts import (
    StudioAgentRegistryResponse,
    StudioAgentRegistryUpdate,
)
from server.app.studio_chat.availability import AgentAvailabilityProbe
from server.app.studio_chat.registry import StudioAgentRegistryStore


def create_studio_agents_admin_router(job_db: JobQueries) -> APIRouter:
    router = APIRouter()
    store = StudioAgentRegistryStore(job_db.path)
    availability_probe = AgentAvailabilityProbe()

    def _response(document: dict[str, Any]) -> StudioAgentRegistryResponse:
        response = StudioAgentRegistryResponse.model_validate(document)
        response.availability = {
            a.id: availability_probe.available(a.command) for a in response.agents
        }
        return response

    @router.get("/admin/studio-agents", response_model=StudioAgentRegistryResponse)
    def get_studio_agents(
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> StudioAgentRegistryResponse:
        return _response(store.get())

    @router.put("/admin/studio-agents", response_model=StudioAgentRegistryResponse)
    def put_studio_agents(
        payload: StudioAgentRegistryUpdate,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> StudioAgentRegistryResponse:
        document = payload.model_dump()
        store.put(document)
        return _response(document)

    return router
