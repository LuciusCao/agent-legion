"""Workspace API intake token management routes (#626).

Issue / list / revoke for the machine-to-machine submission credentials
(schema v83 ``workspace_api_tokens``). Mounting mirrors the worker register
tokens (``require_admin``): issuance is an admin action, a leaked admin
session must not be escalable by a lesser identity, and the scoped-token
guards refuse api-scope identities here anyway (an API token must never
mint a sibling credential — same privilege-extension rule as
/api/studio-agent-tokens).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from server.app.auth.dependencies import reject_studio_agent_scope, require_admin
from server.app.auth.workspace_api_tokens import WorkspaceApiTokenStore
from server.app.db.rowmap import iso_optional
from server.app.routes.workspace_api_token_contracts import (
    CreateWorkspaceApiTokenRequest,
    WorkspaceApiTokenCreatedResponse,
    WorkspaceApiTokenRevokeResponse,
    WorkspaceApiTokensResponse,
    WorkspaceApiTokenSummary,
)


def create_workspace_api_tokens_router(store: WorkspaceApiTokenStore) -> APIRouter:
    router = APIRouter(tags=["workspace-api-tokens"])

    def _summary(entry: dict[str, Any]) -> WorkspaceApiTokenSummary:
        return WorkspaceApiTokenSummary(
            token_id=entry["token_id"],
            workspace_id=entry["workspace_id"],
            label=entry["label"],
            created_at=iso_optional(entry["created_at"]) or "",
            expires_at=iso_optional(entry["expires_at"]),
            revoked=entry["revoked"],
            last_used_at=iso_optional(entry["last_used_at"]),
        )

    @router.post(
        "/workspaces/{workspace_id}/api-tokens",
        status_code=201,
        response_model=WorkspaceApiTokenCreatedResponse,
    )
    def create_api_token(
        workspace_id: str,
        payload: CreateWorkspaceApiTokenRequest,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
        _guard: Annotated[dict[str, Any], Depends(reject_studio_agent_scope)],
    ) -> WorkspaceApiTokenCreatedResponse:
        expires_at = (
            datetime.now(UTC) + timedelta(hours=payload.ttl_hours) if payload.ttl_hours else None
        )
        try:
            token_id, plaintext = store.issue_api_token(
                workspace_id=workspace_id, label=payload.label, expires_at=expires_at
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return WorkspaceApiTokenCreatedResponse(
            token_id=token_id,
            api_token=plaintext,
            workspace_id=workspace_id,
            label=payload.label,
        )

    @router.get(
        "/workspaces/{workspace_id}/api-tokens",
        response_model=WorkspaceApiTokensResponse,
    )
    def list_api_tokens(
        workspace_id: str,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> WorkspaceApiTokensResponse:
        return WorkspaceApiTokensResponse(
            tokens=[_summary(entry) for entry in store.list_api_tokens(workspace_id)]
        )

    @router.delete(
        "/workspaces/{workspace_id}/api-tokens/{token_id}",
        response_model=WorkspaceApiTokenRevokeResponse,
    )
    def revoke_api_token(
        workspace_id: str,
        token_id: str,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> WorkspaceApiTokenRevokeResponse:
        # workspace_id scopes the revoke; a foreign-workspace id fails like
        # an unknown one (no cross-workspace existence leak).
        if not store.revoke_api_token(token_id, workspace_id=workspace_id):
            raise HTTPException(status_code=404, detail="API token not found")
        return WorkspaceApiTokenRevokeResponse(token_id=token_id, revoked=True)

    return router
