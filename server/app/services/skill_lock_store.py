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

写与 upgrade 重验共享 ``skill-lock`` 全域 advisory 锁（#759 P2-B）：
``put_lock`` 在写事务内先取 ``pg_advisory_xact_lock(hashtext('skill-lock'))``
再 upsert；upgrade 的 plan 阶段走 ``get_lock_locked``（短事务取锁+读），
guard 事务内重验由 guard 连接持锁后走无锁的 ``get_lock``。dispatch 热
路径的读（SkillManager doc cache）不进本域。
"""

from __future__ import annotations

from server.app.db.dialect import ConnectSource
from server.app.jobs.queries.global_settings import (
    SKILL_LOCK_ADVISORY_SCOPE,
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

    def get_lock_locked(self) -> SkillsLock | None:
        """skill-lock advisory 域内的读（upgrade plan 阶段的短事务读法）。

        取锁+读在同一短事务（#759 P2-B）：并发 relock 被等完，读到的
        文档不旧于任何在本读前已完成的 relock。guard 事务内重验不走本
        方法（xact 锁不可跨连接重入）——由 guard 连接先取锁、再走无锁
        的 ``get_lock``。dispatch 热路径保持无锁读。
        """
        document = self._kv().get_global_settings_document_under_lock(
            LOCK_KEY, SKILL_LOCK_ADVISORY_SCOPE
        )
        return None if document is None else SkillsLock.model_validate(document)

    def put_lock(self, lock: SkillsLock) -> None:
        # 写与 skill-lock advisory 锁同一事务（#759 P2-B）：upgrade guard
        # 重验持锁期间 relock/首次 pin 被挡住，guard 提交后的 relock 对
        # 下一次 upgrade 可见（语义正确）。进程内 threading 锁
        # （SkillManager._lock_write_lock）保留，管的是 RMW 的读旧基。
        self._kv().put_global_settings_document_under_lock(
            LOCK_KEY, lock.model_dump(), SKILL_LOCK_ADVISORY_SCOPE
        )

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

    def get_lock_locked(self) -> SkillsLock | None:
        """接口对齐（#759 P2-B 的 advisory 域读法）：内存实现无并发域。"""
        return self.get_lock()

    def put_lock(self, lock: SkillsLock) -> None:
        self._lock = lock.model_copy(deep=True)
