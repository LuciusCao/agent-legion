from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from server.app.auth.dependencies import get_current_user
from server.app.jobs import JobQueries
from server.app.routes.skill_catalog_route import (
    require_skill_key_in_workspace,
    require_skill_scope_binding,
)
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

    Both endpoints take an absolute skill path under the skills base dir plus
    the caller's ``workspace_id`` (the authorization scope — the path's
    first base-relative segment is a GROUP name that only coincidentally
    matches a workspace for create_skill-authored skills; the demo's group
    differs from its workspace id). The query parameter doubles as the
    membership scope for the router-level guard; the handler adds the
    scoped-token binding and the workspace-directory strictness (#710,
    codex P1/P2 on #745)."""
    router = APIRouter()

    def _validator() -> SkillValidator:
        # Per-request build: test fixtures monkeypatch build_skill_manager
        # after the router is created, and the manager is cheap to build.
        manager = build_skill_manager(job_db, settings.skills_runs_dir)
        return SkillValidator(manager.base_dir, manager.load_lock)

    def _base_dir() -> Path:
        return build_skill_manager(job_db, settings.skills_runs_dir).base_dir.expanduser().resolve()

    def _key_of_path(raw_path: str) -> str | None:
        """The skill key (two base-relative segments) for an absolute skill
        path, or None when the path does not live under the base — the
        validator's own resolution then answers with its NotFound-shaped
        result and the guards stay out of the way."""
        try:
            relative = Path(raw_path.strip()).expanduser().resolve().relative_to(_base_dir())
        except ValueError:
            return None
        return "/".join(relative.parts[:2]) if len(relative.parts) >= 2 else None

    @router.post("/skills/validate", response_model=SkillValidateResponse)
    def validate_skill(
        request: SkillValidateRequest,
        http_request: Request,
        user: Annotated[dict[str, Any], Depends(get_current_user)],
        workspace_id: str,
    ) -> SkillValidateResponse:
        require_skill_scope_binding(workspace_id, user)
        key = _key_of_path(request.path)
        if key is not None:
            require_skill_key_in_workspace(http_request, key, workspace_id)
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
        workspace_id: str,
        request: Request,
        user: Annotated[dict[str, Any], Depends(get_current_user)],
    ) -> SkillTagsResponse:
        require_skill_scope_binding(workspace_id, user)
        key = _key_of_path(path)
        if key is not None:
            require_skill_key_in_workspace(request, key, workspace_id)
        result = _validator().list_tags(path)
        return SkillTagsResponse(
            path=result.path, tags=list(result.tags), latest_tag=result.latest_tag
        )

    return router
