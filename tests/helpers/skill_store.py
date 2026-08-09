"""In-memory skill source store builders for SkillManager unit tests."""

from __future__ import annotations

from typing import Any

from server.app.services.skill_source_store import InMemorySkillSourceStore
from server.app.skills.config import SkillsConfig, SkillsLock


def memory_skill_store(
    skills: dict[str, dict[str, str]] | None = None,
    lock: dict[str, Any] | None = None,
) -> InMemorySkillSourceStore:
    """Build an in-memory store from raw dicts (retired yaml shapes).

    ``skills`` maps skill keys to ``{repo, ref}``; ``lock`` is a full lock
    document (``{version, resolved_at?, skills: {key: {repo, ref, commit}}}``).
    """
    sources = SkillsConfig.model_validate({"skills": skills or {}})
    lock_model = SkillsLock.model_validate(lock) if lock is not None else None
    return InMemorySkillSourceStore(sources=sources, lock=lock_model)
