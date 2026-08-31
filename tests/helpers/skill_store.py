"""In-memory skill lock store builders for SkillManager unit tests."""

from __future__ import annotations

from typing import Any

from server.app.services.skill_lock_store import InMemorySkillLockStore
from server.app.skills.config import SkillsLock


def memory_skill_store(lock: dict[str, Any] | None = None) -> InMemorySkillLockStore:
    """Build an in-memory lock-only store from a raw lock document.

    ``lock`` is a full lock document (``{version, resolved_at?, skills: {key:
    {repo, refs}}}`` — the legacy v1 ``{repo, ref, commit}`` entry shape
    upgrades on validation, issue #76). Since #322 the store holds only the
    lock: skill locations derive from the base dir + key, and ``latest``
    refs never touch the lock.
    """
    lock_model = SkillsLock.model_validate(lock) if lock is not None else None
    return InMemorySkillLockStore(lock=lock_model)
