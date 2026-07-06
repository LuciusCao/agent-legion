from __future__ import annotations

from fastapi import APIRouter, HTTPException

from server.app.routes.token_usage_contracts import (
    TokenUsageJobResponse,
    TokenUsageWorkspaceResponse,
)
from server.app.routes.token_usage_run_contracts import TokenUsageRunResponse
from server.app.services.token_usage import (
    build_job_usage_response,
    build_run_usage_response,
    build_workspace_usage_response,
)
from server.app.settings import Settings


def create_token_usage_router(job_queries, settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get("/jobs/{job_id}/runs/{run_id}/token-usage", response_model=TokenUsageRunResponse)
    def get_run_token_usage(job_id: str, run_id: int) -> TokenUsageRunResponse:
        job = job_queries.job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        run = job_queries.job_db.get_node_run(job_id, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return TokenUsageRunResponse(
            **build_run_usage_response(job_queries.job_db, run, settings.config)
        )

    @router.get("/jobs/{job_id}/token-usage", response_model=TokenUsageJobResponse)
    def get_job_token_usage(job_id: str) -> TokenUsageJobResponse:
        job = job_queries.job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return TokenUsageJobResponse(
            **build_job_usage_response(job_queries.job_db, job_id, settings.config)
        )

    @router.get(
        "/workspaces/{workspace_id}/token-usage", response_model=TokenUsageWorkspaceResponse
    )
    def get_workspace_token_usage(
        workspace_id: str,
        node_key: str | None = None,
        job_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        skill_version: str | None = None,
        group_by: str = "node",
        limit: int = 100,
    ) -> TokenUsageWorkspaceResponse:
        workspace = job_queries.job_db.get_workspace(workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace not found")
        return TokenUsageWorkspaceResponse(
            **build_workspace_usage_response(
                job_queries.job_db,
                workspace_id,
                settings.config,
                node_key=node_key,
                job_id=job_id,
                provider=provider,
                model=model,
                skill_version=skill_version,
                group_by=group_by,
                limit=limit,
            )
        )

    return router
