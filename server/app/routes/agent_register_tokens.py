"""Admin routes for workspace-scoped Agent Worker registration tokens.

Split from agent_workers.py for the file-size budget: the /agent-register-tokens
management surface (issue / list / delete, all require_admin) lives here;
worker registration and the execution data plane stay in agent_workers.py.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from server.app.agent_workers import AgentWorkerRegistry
from server.app.auth.dependencies import require_admin
from server.app.routes.agent_workers_contracts import (
    AgentRegisterTokenCreatedResponse,
    AgentRegisterTokenDeleteResponse,
    AgentRegisterTokensResponse,
    CreateAgentRegisterTokenRequest,
)


def create_agent_register_tokens_router(registry: AgentWorkerRegistry) -> APIRouter:
    router = APIRouter(tags=["agent-workers"])

    @router.post(
        "/agent-register-tokens",
        status_code=201,
        response_model=AgentRegisterTokenCreatedResponse,
    )
    def create_register_token(
        payload: CreateAgentRegisterTokenRequest,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> AgentRegisterTokenCreatedResponse:
        try:
            token_id, plaintext = registry.issue_register_token(
                workspace_id=payload.workspace_id, label=payload.label
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return AgentRegisterTokenCreatedResponse(
            token_id=token_id,
            register_token=plaintext,
            workspace_id=payload.workspace_id,
            label=payload.label,
        )

    @router.get("/agent-register-tokens", response_model=AgentRegisterTokensResponse)
    def list_register_tokens(
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> AgentRegisterTokensResponse:
        return AgentRegisterTokensResponse.model_validate(
            {"tokens": registry.list_register_tokens()}
        )

    @router.delete(
        "/agent-register-tokens/{token_id}",
        response_model=AgentRegisterTokenDeleteResponse,
    )
    def delete_register_token(
        token_id: str,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> AgentRegisterTokenDeleteResponse:
        """Hard-delete a key and cascade-cut dependent workers: workers left
        without any live key lose their registration record in the same
        transaction (their credential dies immediately); workers with other
        live keys are narrowed to the surviving keys' scope."""
        cascaded = registry.delete_register_token(token_id)
        if cascaded is None:
            raise HTTPException(status_code=404, detail="Agent register token not found")
        return AgentRegisterTokenDeleteResponse(
            token_id=token_id, deleted=True, cascaded_worker_ids=cascaded
        )

    return router
