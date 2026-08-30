"""Database-backed storage for the Pi skill sources and skill lock documents.

The skill source declarations (``{repo, ref}`` per skill, retired
``config/skills.yaml``) and the resolved lock (``+ commit``, retired
``config/skills.lock``) are product settings: they live only in the
``global_settings`` table under the ``skill_sources`` / ``skill_lock`` keys
and are managed through the DB (refresh via ``make skills-lock``); no yaml
fallback exists beyond the one-time import at startup
(``server.app.skills.seed``).

SQL lives in the queries layer (``global_settings`` KV mixin, issue #281);
this store is the domain facade doing the pydantic conversions.
"""

from __future__ import annotations

from server.app.db.dialect import ConnectSource
from server.app.jobs.queries.global_settings import (
    GlobalSettingsKVQueriesMixin,
    global_settings_kv_from_dsn,
)
from server.app.skills.builtin_sources import BUILTIN_SKILL_LOCK, BUILTIN_SKILL_SOURCES
from server.app.skills.config import SkillsConfig, SkillsLock

SOURCES_KEY = "skill_sources"
LOCK_KEY = "skill_lock"


class SkillSourceStore:
    """Read/write the skill source documents in ``global_settings``."""

    def __init__(self, database_dsn: ConnectSource) -> None:
        # database_dsn: JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187).
        self._dsn = database_dsn

    def get_sources(self) -> SkillsConfig | None:
        """Return the declared skill sources, or None when never seeded."""
        document = self._kv().get_global_settings_document(SOURCES_KEY)
        return None if document is None else SkillsConfig.model_validate(document)

    def put_sources(self, config: SkillsConfig) -> None:
        self._kv().put_global_settings_document(SOURCES_KEY, config.model_dump())

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


class InMemorySkillSourceStore:
    """SkillSourceStore test/default double: same contract, no database.

    Used by ``RuntimeDependencies``' default skill manager (no DSN available at
    that layer) and by unit tests that exercise ``SkillManager`` without
    PostgreSQL.
    """

    def __init__(
        self,
        sources: SkillsConfig | None = None,
        lock: SkillsLock | None = None,
    ) -> None:
        self._sources = sources
        self._lock = lock

    @classmethod
    def with_builtins(cls) -> InMemorySkillSourceStore:
        return cls(
            sources=BUILTIN_SKILL_SOURCES.model_copy(deep=True),
            lock=BUILTIN_SKILL_LOCK.model_copy(deep=True),
        )

    def get_sources(self) -> SkillsConfig | None:
        return None if self._sources is None else self._sources.model_copy(deep=True)

    def put_sources(self, config: SkillsConfig) -> None:
        self._sources = config.model_copy(deep=True)

    def get_lock(self) -> SkillsLock | None:
        return None if self._lock is None else self._lock.model_copy(deep=True)

    def put_lock(self, lock: SkillsLock) -> None:
        self._lock = lock.model_copy(deep=True)
