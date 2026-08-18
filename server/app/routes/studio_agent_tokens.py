"""Admin self-service studio-agent token management (/api/studio-agent-tokens).

Admins mint, list, and revoke their own long-lived studio-agent scoped tokens
(origin='user', schema v42) for external agents such as the MCP server
(``server.app.mcp_server``); admin-only since P4 so members cannot mint their
way onto the Studio authoring tool surface. Run-scoped tokens (origin='run')
never appear here. The raw token is returned exactly once at mint time;
list/revoke only ever see the public id. Scoped tokens themselves are
refused — a short-lived run token must not be able to mint long-lived
credentials (privilege extension), so every endpoint also mounts
``reject_studio_agent_scope``.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from server.app.auth.dependencies import reject_studio_agent_scope, require_admin
from server.app.jobs import JobQueries
from server.app.routes.studio_agent_token_contracts import (
    StudioAgentTokenEntry,
    StudioAgentTokenMintRequest,
    StudioAgentTokenMintResponse,
    StudioAgentTokenRevokeResponse,
    StudioAgentTokensResponse,
)
from server.app.services.studio_agent_tokens import StudioAgentTokensService


def create_studio_agent_tokens_router(job_db: JobQueries) -> APIRouter:
    router = APIRouter()

    def _service() -> StudioAgentTokensService:
        return StudioAgentTokensService(job_db)

    @router.post(
        "/studio-agent-tokens",
        response_model=StudioAgentTokenMintResponse,
        status_code=201,
    )
    def mint_token(
        payload: StudioAgentTokenMintRequest,
        user: Annotated[dict[str, Any], Depends(require_admin)],
        _guard: Annotated[dict[str, Any], Depends(reject_studio_agent_scope)],
    ) -> StudioAgentTokenMintResponse:
        minted = _service().mint(str(user["id"]), ttl_hours=payload.ttl_hours)
        return StudioAgentTokenMintResponse(**minted)

    @router.get("/studio-agent-tokens", response_model=StudioAgentTokensResponse)
    def list_tokens(
        user: Annotated[dict[str, Any], Depends(require_admin)],
        _guard: Annotated[dict[str, Any], Depends(reject_studio_agent_scope)],
    ) -> StudioAgentTokensResponse:
        entries = _service().list(str(user["id"]))
        return StudioAgentTokensResponse(
            tokens=[StudioAgentTokenEntry(**entry) for entry in entries]
        )

    @router.delete(
        "/studio-agent-tokens/{token_id}",
        response_model=StudioAgentTokenRevokeResponse,
    )
    def revoke_token(
        token_id: str,
        user: Annotated[dict[str, Any], Depends(require_admin)],
        _guard: Annotated[dict[str, Any], Depends(reject_studio_agent_scope)],
    ) -> StudioAgentTokenRevokeResponse:
        if not _service().revoke(str(user["id"]), token_id):
            # 404 for both unknown and foreign ids: existence is not leaked.
            raise HTTPException(status_code=404, detail="Token not found")
        return StudioAgentTokenRevokeResponse(id=token_id, revoked=True)

    return router
