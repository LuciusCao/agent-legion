"""Database-backed storage for the Pi skill sources and skill lock documents.

The skill source declarations (``{repo, ref}`` per skill, retired
``config/skills.yaml``) and the resolved lock (``+ commit``, retired
``config/skills.lock``) are product settings: they live only in the
``global_settings`` table under the ``skill_sources`` / ``skill_lock`` keys
and are managed through the DB (refresh via ``make skills-lock``); no yaml
fallback exists beyond the one-time import at startup
(``server.app.skills.seed``).
"""

from __future__ import annotations

import json
from typing import Any, cast

from server.app.db.dialect import ConnectSource
from server.app.db.transaction import read_connection, write_transaction
from server.app.skills.builtin_sources import BUILTIN_SKILL_LOCK, BUILTIN_SKILL_SOURCES
from server.app.skills.config import SkillsConfig, SkillsLock

SOURCES_KEY = "skill_sources"
LOCK_KEY = "skill_lock"


class SkillSourceStore:
    """Read/write the skill source documents in ``global_settings``."""

    def __init__(self, database_dsn: ConnectSource) -> None:
        # database_dsn: JobQueries facade or bare DSN (BOUNDARY-DATA-001, #187).
        self._dsn = database_dsn

    def _read(self, key: str) -> dict[str, Any] | None:
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                "select value from global_settings where key=%s",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return cast(dict[str, Any], json.loads(str(row["value"])))

    def _write(self, key: str, document: dict[str, Any]) -> None:
        payload = json.dumps(document)
        with write_transaction(self._dsn) as conn:
            conn.execute(
                """
                insert into global_settings(key, value) values (%s, %s)
                on conflict(key)
                do update set value=excluded.value, updated_at=current_timestamp
                """,
                (key, payload),
            )

    def get_sources(self) -> SkillsConfig | None:
        """Return the declared skill sources, or None when never seeded."""
        document = self._read(SOURCES_KEY)
        return None if document is None else SkillsConfig.model_validate(document)

    def put_sources(self, config: SkillsConfig) -> None:
        self._write(SOURCES_KEY, config.model_dump())

    def get_lock(self) -> SkillsLock | None:
        """Return the resolved skill lock, or None when never seeded."""
        document = self._read(LOCK_KEY)
        return None if document is None else SkillsLock.model_validate(document)

    def put_lock(self, lock: SkillsLock) -> None:
        self._write(LOCK_KEY, lock.model_dump())


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
