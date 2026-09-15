"""User-facing shared-material propagation endpoint (issue #673).

``POST /api/workspaces/{id}/skills-shared/propagate`` — the write
counterpart of the read-only view: copy the selected (or all) mapped
shared sources into each mapped skill repo, commit and tag a new patch
version per skill. Mounted via ``secured()``: ``require_workspace_access``
requires the editor role for non-safe methods. The heavy lifting lives in
``services/skill_shared_propagate`` (per-skill isolation; the DB skill
lock is never touched).
"""

from __future__ import annotations

from fastapi import APIRouter

from server.app.jobs import JobQueries
from server.app.routes.job_http import raise_job_http_error
from server.app.routes.workspace_shared_materials_propagate_contracts import (
    SharedMaterialPropagateSkillResult,
    SharedMaterialsPropagateRequest,
    SharedMaterialsPropagateResponse,
)
from server.app.services.job_errors import JobServiceError, NotFoundError
from server.app.services.skill_shared_propagate import propagate_shared_materials
from server.app.settings import Settings


def create_workspace_shared_materials_propagate_router(
    job_db: JobQueries, settings: Settings
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/workspaces/{workspace_id}/skills-shared/propagate",
        response_model=SharedMaterialsPropagateResponse,
    )
    def propagate_shared(
        workspace_id: str, payload: SharedMaterialsPropagateRequest
    ) -> SharedMaterialsPropagateResponse:
        if job_db.get_workspace(workspace_id) is None:
            raise_job_http_error(NotFoundError("Workspace not found"))
        try:
            result = propagate_shared_materials(
                workspace_id, payload.sources, runs_dir=settings.skills_runs_dir
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return SharedMaterialsPropagateResponse(
            workspace_id=workspace_id,
            results=[SharedMaterialPropagateSkillResult(**vars(r)) for r in result.results],
        )

    return router
