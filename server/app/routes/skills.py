from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Query

from server.app.auth.skill_access import require_skill_path_in_workspace
from server.app.jobs import JobQueries
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

    The router-level guard owns query-scope membership and token binding;
    the auth path helper adds key ownership only for paths under the base.
    """
    router = APIRouter()

    def _validator() -> SkillValidator:
        # Build lazily: tests monkeypatch the manager after router creation.
        manager = build_skill_manager(job_db, settings.skills_runs_dir)
        return SkillValidator(manager.base_dir, manager.load_lock)

    def _guard_path(raw_path: str, workspace_id: str) -> None:
        manager = build_skill_manager(job_db, settings.skills_runs_dir)
        require_skill_path_in_workspace(job_db, raw_path, manager.base_dir, workspace_id)

    @router.post("/skills/validate", response_model=SkillValidateResponse)
    def validate_skill(
        request: SkillValidateRequest,
        workspace_id: Annotated[str, Query(min_length=1)],
    ) -> SkillValidateResponse:
        _guard_path(request.path, workspace_id)
        result = _validator().validate(request.path)
        return SkillValidateResponse(**asdict(result))

    @router.get("/skills/tags", response_model=SkillTagsResponse)
    def list_skill_tags(
        path: str,
        workspace_id: Annotated[str, Query(min_length=1)],
    ) -> SkillTagsResponse:
        _guard_path(path, workspace_id)
        result = _validator().list_tags(path)
        return SkillTagsResponse(**asdict(result))

    return router
