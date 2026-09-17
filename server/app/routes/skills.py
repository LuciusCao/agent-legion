from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from server.app.auth.dependencies import get_current_user
from server.app.jobs import JobQueries
from server.app.routes.skill_catalog_route import require_skill_workspace_member
from server.app.routes.skill_contracts import (
    SkillTagsResponse,
    SkillValidateRequest,
    SkillValidateResponse,
)
from server.app.services.skill_validator import SkillValidator
from server.app.settings import Settings
from server.app.skills.runtime import build_skill_manager


def create_skills_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    """Skill path validation + tag discovery for the Studio Agent editor.

    Both endpoints take an absolute skill path under the skills base dir
    (``<base>/<workspace>/<capability>``); like the catalog detail route,
    they carry no ``workspace_id`` path parameter, so the workspace segment
    is extracted from the path and membership-checked (#710 red-team V1:
    these surfaces leaked any workspace's skill tags/metadata to any
    logged-in user)."""
    router = APIRouter()

    def _validator() -> SkillValidator:
        # Per-request build: test fixtures monkeypatch build_skill_manager
        # after the router is created, and the manager is cheap to build.
        manager = build_skill_manager(job_db, settings.skills_runs_dir)
        return SkillValidator(manager.base_dir, manager.load_lock)

    def _base_dir() -> Path:
        return build_skill_manager(job_db, settings.skills_runs_dir).base_dir.expanduser().resolve()

    def _workspace_of_path(raw_path: str) -> str | None:
        """First path segment under the skills base dir (the workspace), or
        None when the path does not live under the base — the validator's
        own resolution then answers with its NotFound-shaped result, and
        the membership check stays out of the way."""
        try:
            relative = Path(raw_path.strip()).expanduser().resolve().relative_to(_base_dir())
        except ValueError:
            return None
        return relative.parts[0] if relative.parts else None

    @router.post("/skills/validate", response_model=SkillValidateResponse)
    def validate_skill(
        request: SkillValidateRequest,
        http_request: Request,
        user: Annotated[dict[str, Any], Depends(get_current_user)],
    ) -> SkillValidateResponse:
        workspace_id = _workspace_of_path(request.path)
        if workspace_id is not None:
            require_skill_workspace_member(http_request, workspace_id, user)
        result = _validator().validate(request.path)
        return SkillValidateResponse(
            valid=result.valid,
            path=result.path,
            skill_key=result.skill_key,
            error=result.error,
            tags=list(result.tags),
            latest_tag=result.latest_tag,
            locked_ref=result.locked_ref,
            warnings=list(result.warnings),
        )

    @router.get("/skills/tags", response_model=SkillTagsResponse)
    def list_skill_tags(
        path: str,
        request: Request,
        user: Annotated[dict[str, Any], Depends(get_current_user)],
    ) -> SkillTagsResponse:
        workspace_id = _workspace_of_path(path)
        if workspace_id is not None:
            require_skill_workspace_member(request, workspace_id, user)
        result = _validator().list_tags(path)
        return SkillTagsResponse(
            path=result.path, tags=list(result.tags), latest_tag=result.latest_tag
        )

    return router
