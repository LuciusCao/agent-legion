"""Admin routes for the Studio chat ACP agent registry (phase 3 chunk 4).

Registry 文档存于 global_settings 的 studio_agents 键（为何不并入实例
设置见 registry 模块）。Admin-only：agent 命令行由此进入系统（#332 起带
目录探测）。
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status

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

    def _response(document: dict[str, Any]) -> StudioAgentRegistryResponse:
        detection = {k: vars(v) for k, v in detector.detect().items()}
        response = StudioAgentRegistryResponse.model_validate(
            document | {"detection": detection, "revision": registry_revision(document)}
        )
        avail = {a.id: availability_probe.available(a.command) for a in response.agents}
        response.availability = avail
        return response

    @router.get("/admin/studio-agents", response_model=StudioAgentRegistryResponse)
    def get_studio_agents(
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> StudioAgentRegistryResponse:
        return _response(store.get())

    @router.put("/admin/studio-agents", response_model=StudioAgentRegistryResponse)
    def put_studio_agents(
        payload: StudioAgentRegistryUpdate,
        response: Response,
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
            # #355（方案 1）：行锁内版本比对失败——快照后已有新写入（典型为
            # 探测合并进新 detected 行），整份覆盖会静默删行；409 附当前
            # 注册表让前端提示刷新。
            response.status_code = status.HTTP_409_CONFLICT
            return _response(store.get())
        # 审核 P2：200 用 RMW 事务内合并后的文档——revision 即本次写入
        # （事务外 get 可能把并发写入者的结果冒充本次保存返回）。
        return _response(merged)

    @router.post("/admin/studio-agents/redetect", response_model=StudioAgentRegistryResponse)
    def redetect_studio_agents(
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> StudioAgentRegistryResponse:
        return _response(agent_catalog.redetect_and_merge(store, detector))

    return router
