"""Database-backed storage for the skill lock document.

The resolved skill lock (per-skill ``{repo, refs: {ref -> commit}}``, retired
``config/skills.lock``) is a product setting: it lives only in the
``global_settings`` table under the ``skill_lock`` key and is managed through
the DB (refresh via ``make skills-lock``, auto-lock on first dispatch of a
pinned ref). The retired ``skill_sources`` registry (repo + default ref per
skill) was removed in #322: a skill's location derives from the skills root +
key, and an empty/``latest`` node ref follows the repo's live HEAD without
ever touching this lock.

SQL lives in the queries layer (``global_settings`` KV mixin, issue #281);
this store is the domain facade doing the pydantic conversions.
"""

from __future__ import annotations

from server.app.db.dialect import ConnectSource
from server.app.jobs.queries.global_settings import (
    GlobalSettingsKVQueriesMixin,
    global_settings_kv_from_dsn,
)
from server.app.skills.config import SkillsLock

LOCK_KEY = "skill_lock"


class SkillLockStore:
    """Read/write the skill lock document in ``global_settings``."""

    def __init__(self, database_dsn: ConnectSource) -> None:
        # database_dsn: JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187).
        self._dsn = database_dsn

    def get_lock(self) -> SkillsLock | None:
        """Return the resolved skill lock, or None when never seeded."""
        document = self._kv().get_global_settings_document(LOCK_KEY)
        return None if document is None else SkillsLock.model_validate(document)

    def put_lock(self, lock: SkillsLock) -> None:
        self._kv().put_global_settings_document(LOCK_KEY, lock.model_dump())

    def _kv(self) -> GlobalSettingsKVQueriesMixin:
        """The KV accessor: the facade itself, or an adapter for a bare DSN
        (``ConnectSource`` contract, #187; SQL centralization #281)."""
        if isinstance(self._dsn, str):
            return global_settings_kv_from_dsn(self._dsn)
        return self._dsn


class InMemorySkillLockStore:
    """SkillLockStore test/default double: same contract, no database.

    Used by ``RuntimeDependencies``' default skill manager (no DSN available at
    that layer) and by unit tests that exercise ``SkillManager`` without
    PostgreSQL.
    """

    def __init__(self, lock: SkillsLock | None = None) -> None:
        self._lock = lock

    def get_lock(self) -> SkillsLock | None:
        return None if self._lock is None else self._lock.model_copy(deep=True)

    def put_lock(self, lock: SkillsLock) -> None:
        self._lock = lock.model_copy(deep=True)
