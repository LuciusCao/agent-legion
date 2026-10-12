"""Admin routes for the Studio chat ACP agent registry (phase 3 chunk 4).

The registry document lives in ``global_settings`` under the
``studio_agents`` key (see server.app.studio_chat.registry for why it is not
folded into the monolithic instance settings document). Admin-only: this is
where agent command lines enter the system (#332 adds catalog detection, #355
the PUT stale-snapshot conflict check).
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from server.app.auth.dependencies import require_admin
from server.app.jobs import JobQueries
from server.app.routes.studio_agents_admin_contracts import (
    StudioAgentRegistryResponse,
    StudioAgentRegistryUpdate,
)
from server.app.studio_chat import agent_catalog
from server.app.studio_chat.availability import AgentAvailabilityProbe
from server.app.studio_chat.registry import (
    RegistryVersionMismatch,
    StudioAgentRegistryStore,
    api_base_host_is_internal,
    registry_revision,
)

logger = logging.getLogger(__name__)


def create_studio_agents_admin_router(job_db: JobQueries) -> APIRouter:
    router = APIRouter()
    store = StudioAgentRegistryStore(job_db)
    availability_probe = AgentAvailabilityProbe()
    detector = agent_catalog.AgentCatalogDetector()

    def _response(document: dict[str, Any], revision: str) -> StudioAgentRegistryResponse:
        detection = {k: vars(v) for k, v in detector.detect().items()}
        response = StudioAgentRegistryResponse.model_validate(
            document | {"detection": detection, "revision": revision}
        )
        response.availability = {
            a.id: availability_probe.available(a.command) for a in response.agents
        }
        return response

    @router.get("/admin/studio-agents", response_model=StudioAgentRegistryResponse)
    def get_studio_agents(
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> StudioAgentRegistryResponse:
        document, revision = store.get_with_revision()
        return _response(document, revision)

    @router.put("/admin/studio-agents", response_model=StudioAgentRegistryResponse)
    def put_studio_agents(
        payload: StudioAgentRegistryUpdate,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> StudioAgentRegistryResponse:
        document = payload.model_dump(exclude={"revision"})
        # api_base is the egress target for per-session scoped tokens (#158):
        # an external host is allowed (remote deployments) but loud, because a
        # misconfiguration here leaks tokens outside the network.
        if not api_base_host_is_internal(str(document["api_base"])):
            logger.warning(
                "studio agent registry api_base points outside the internal network: %s "
                "(scoped session tokens will be sent to this host)",
                document["api_base"],
            )
        # RMW with server-side source re-derivation (#332): clients need not
        # round-trip source, and provenance cannot be forged via the API.
        try:
            merged = store.conditional_put(
                payload.revision, agent_catalog.merge_manual_edit, document
            )
        except RegistryVersionMismatch:
            # #355：行锁内版本比对失败——快照后已有新写入（典型为探测合并进
            # 新 detected 行），整份覆盖会静默删行；409 的 detail 携带当前
            # 注册表（含新版本）让前端提示刷新（#719：httpError 只保留
            # detail，冲突文档经 detail 传递）。
            current, revision = store.get_with_revision()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_response(current, revision).model_dump(mode="json"),
            ) from None
        # 200 响应用 RMW 事务内合并后的文档——revision 即本次写入（事务外
        # get 可能把并发写入者的结果冒充本次保存返回）。
        return _response(merged, registry_revision(merged))

    @router.post("/admin/studio-agents/redetect", response_model=StudioAgentRegistryResponse)
    def redetect_studio_agents(
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> StudioAgentRegistryResponse:
        agent_catalog.redetect_and_merge(store, detector)
        document, revision = store.get_with_revision()
        return _response(document, revision)

    return router
