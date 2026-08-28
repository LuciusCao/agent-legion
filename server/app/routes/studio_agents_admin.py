"""Admin routes for the Studio chat ACP agent registry (phase 3 chunk 4).

The registry document lives in ``global_settings`` under the
``studio_agents`` key (see server.app.studio_chat.registry for why it is not
folded into the monolithic instance settings document). Admin-only: this is
where agent command lines enter the system.
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import require_admin
from server.app.jobs import JobQueries
from server.app.routes.studio_agents_admin_contracts import (
    StudioAgentRegistryResponse,
    StudioAgentRegistryUpdate,
)
from server.app.studio_chat.availability import AgentAvailabilityProbe
from server.app.studio_chat.registry import StudioAgentRegistryStore, api_base_host_is_internal

logger = logging.getLogger(__name__)


def create_studio_agents_admin_router(job_db: JobQueries) -> APIRouter:
    router = APIRouter()
    store = StudioAgentRegistryStore(job_db)
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
        # api_base is the egress target for per-session scoped tokens (#158):
        # an external host is allowed (remote deployments) but loud, because a
        # misconfiguration here leaks tokens outside the network.
        if not api_base_host_is_internal(str(document["api_base"])):
            logger.warning(
                "studio agent registry api_base points outside the internal network: %s "
                "(scoped session tokens will be sent to this host)",
                document["api_base"],
            )
        store.put(document)
        return _response(document)

    return router
