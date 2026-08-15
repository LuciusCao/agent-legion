from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import require_admin
from server.app.executors._pi_skill import build_skill_manager
from server.app.routes.skill_source_contracts import (
    SkillSourceEntry,
    SkillSourcesResponse,
    SkillSourceUpdate,
)
from server.app.services.skill_source_store import SkillSourceStore
from server.app.settings import Settings
from server.app.skills.config import SkillsConfig, SkillSourceConfig
from server.app.skills.lock import refresh_lock


def _merged_view(store: SkillSourceStore) -> SkillSourcesResponse:
    """Declared sources joined with the resolved lock; mismatch means stale."""
    sources = store.get_sources() or SkillsConfig()
    lock = store.get_lock()
    locked = {} if lock is None else lock.skills
    resolved_at = None if lock is None else lock.resolved_at
    entries: list[SkillSourceEntry] = []
    for key in sorted(sources.skills):
        source = sources.skills[key]
        entry = locked.get(key)
        stale = entry is None or entry.repo != source.repo or entry.ref != source.ref
        entries.append(
            SkillSourceEntry(
                key=key,
                repo=source.repo,
                ref=source.ref,
                locked_commit=None if entry is None else entry.commit,
                resolved_at=resolved_at,
                stale=stale,
            )
        )
    return SkillSourcesResponse(skills=entries)


def create_skill_sources_router(settings: Settings) -> APIRouter:
    """Admin endpoints managing the DB-backed skill sources and lock."""
    router = APIRouter()
    store = SkillSourceStore(settings.database_url)

    @router.get("/admin/skill-sources", response_model=SkillSourcesResponse)
    def get_skill_sources(
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> SkillSourcesResponse:
        return _merged_view(store)

    @router.put("/admin/skill-sources/{skill_key:path}", response_model=SkillSourcesResponse)
    def put_skill_source(
        payload: SkillSourceUpdate,
        skill_key: str,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> SkillSourcesResponse:
        sources = store.get_sources()
        if sources is None:
            # Fresh deployments have no seeded document at all; the first
            # declared source creates it.
            sources = SkillsConfig()
        # Unknown keys are created, not rejected: declaring a brand-new skill
        # source (business or otherwise) must be possible over the API.
        sources.skills[skill_key] = SkillSourceConfig(repo=payload.repo, ref=payload.ref)
        # No implicit relock: the merged view flags the entry stale until the
        # admin refreshes the lock explicitly.
        store.put_sources(sources)
        return _merged_view(store)

    @router.post("/admin/skill-sources/relock", response_model=SkillSourcesResponse)
    def relock_skill_sources(
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> SkillSourcesResponse:
        # Local git resolution over a handful of repos; runs synchronously.
        manager = build_skill_manager(settings.database_url)
        refresh_lock(SkillSourceStore(settings.database_url), manager.base_dir)
        return _merged_view(store)

    return router
