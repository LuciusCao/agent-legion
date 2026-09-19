"""KV access for the ``global_settings`` table (issue #281).

Several services (instance settings, cleanup sweep, token-usage pricing,
studio agent registry, skill lock) each hand-wrote the same
``select value from global_settings where key=%s`` /
``insert ... on conflict(key) do update`` pair. The pair lives here once,
behind the JobQueries facade, and the stores keep only their domain
concerns (pydantic validation, defaults synthesis, cursor aggregation).

Contract held identical to the inlined code it replaces (#281):
- ``get`` returns ``None`` when the key has no row (stores that need an
  empty document normalize at the call site, as they always did);
- JSON parsing failures raise (``json.loads`` is called bare in every
  pre-existing copy — corrupt rows surfaced as exceptions, never silently
  defaulted);
- ``put`` serializes with plain ``json.dumps`` (no ``default=`` hook):
  callers pass plain JSON-able documents, same as before.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

from server.app.jobs.queries.connection import ConnectionQueriesMixin

_UPSERT_SQL = """
    insert into global_settings(key, value) values (%s, %s)
    on conflict(key)
    do update set value=excluded.value, updated_at=current_timestamp
"""

_SELECT_SQL = "select value from global_settings where key=%s"
_SELECT_FOR_UPDATE_SQL = "select value from global_settings where key=%s for update"
_RMW_ENSURE_SQL = """
    insert into global_settings(key, value) values (%s, %s) on conflict(key) do nothing
"""
_ADVISORY_LOCK_SQL = "select pg_advisory_xact_lock(hashtext(%s))"

#: skill-lock 全域 advisory 锁的固定 scope（#759 P2-B）：skill_lock 是
#: 全局单文档（非 per-workspace），写侧（dispatch 首次 pin 与
#: ``make skills-lock`` 重锁，均经 ``SkillLockStore.put_lock``）与
#: upgrade 安全判定的读（plan 短事务 + guard 事务内重验）共享本域。
SKILL_LOCK_ADVISORY_SCOPE = "skill-lock"


def acquire_skill_lock_domain_lock(conn: Any) -> None:
    """在调用方事务内取 skill-lock 全域 advisory 锁（xact 级，提交才释放）。

    锁序（EXEC-GENERATION-001，#759 P2-B）：池级锁 → job-mutation →
    implementation-publication → skill-lock；本域持有者不得在持锁期间
    反向取前两者（写侧 put 只持本锁，guard 侧严格按上序追加）。
    """
    conn.execute(_ADVISORY_LOCK_SQL, (SKILL_LOCK_ADVISORY_SCOPE,))


class GlobalSettingsKVQueriesMixin(ConnectionQueriesMixin):
    """Read/write one JSON document per key in ``global_settings``."""

    def get_global_settings_document(self, key: str) -> dict[str, Any] | None:
        """The stored document, or None when the key has no row yet."""
        with self._connect_read() as conn:
            row = conn.execute(_SELECT_SQL, (key,)).fetchone()
        if row is None:
            return None
        return cast(dict[str, Any], json.loads(str(row["value"])))

    def put_global_settings_document(self, key: str, document: dict[str, Any]) -> None:
        """Replace the stored document (upsert; whole-document semantics)."""
        with self.write() as conn:
            conn.execute(_UPSERT_SQL, (key, json.dumps(document)))

    @staticmethod
    def acquire_skill_lock_domain_lock(conn: Any) -> None:
        """facade 形态（guard 事务内用，比照 upgrade_impl_identity 的静态面）。"""
        acquire_skill_lock_domain_lock(conn)

    def get_global_settings_document_under_lock(
        self, key: str, scope: str
    ) -> dict[str, Any] | None:
        """短事务内「取 advisory 锁 + 读」：读到的文档 ≥ 任何已完成的写。

        取锁会等完并发的未提交写（xact 锁互斥），随后的读因此看不到
        比「本读开始前已完成的写」更旧的文档（#759 P2-B 的 plan 阶段
        读法）。只有 upgrade 安全判定用本变体；dispatch 热路径保持
        无锁读（``get_global_settings_document``）。
        """
        with self.connect() as conn:
            conn.execute(_ADVISORY_LOCK_SQL, (scope,))
            row = conn.execute(_SELECT_SQL, (key,)).fetchone()
        if row is None:
            return None
        return cast(dict[str, Any], json.loads(str(row["value"])))

    def put_global_settings_document_under_lock(
        self, key: str, document: dict[str, Any], scope: str
    ) -> None:
        """写事务内「取 advisory 锁 + upsert」：锁与写同一事务、同生同死。

        拆成两个事务会让 upgrade guard 的重验与其提交之间插进已落地的
        写（#759 P2-B 的 relock 窗口）——锁必须覆盖到写提交。
        """
        with self.write() as conn:
            conn.execute(_ADVISORY_LOCK_SQL, (scope,))
            conn.execute(_UPSERT_SQL, (key, json.dumps(document)))

    def delete_global_settings_document(self, key: str) -> bool:
        """Delete the key's row; True when a row was actually removed."""
        with self.write() as conn:
            cursor = conn.execute("delete from global_settings where key=%s", (key,))
        return cursor.rowcount > 0

    def update_global_settings_document(
        self,
        key: str,
        updater: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        """Read-modify-write one document inside a single transaction.

        The read runs SELECT ... FOR UPDATE after an insert-if-absent, so
        concurrent RMWs on the same key queue on the row lock (and on the
        unique index for first creators) instead of last-wins overwriting
        each other (#281, codex P1 on #332). Read semantics, return value,
        and exception shapes are unchanged.
        """
        with self.connect() as conn:
            conn.execute(_RMW_ENSURE_SQL, (key, "{}"))
            row = conn.execute(_SELECT_FOR_UPDATE_SQL, (key,)).fetchone()
            document = cast(
                dict[str, Any], json.loads(str(row["value"])) if row is not None else {}
            )
            conn.execute(_UPSERT_SQL, (key, json.dumps(updater(document))))


def global_settings_kv_from_dsn(dsn: str) -> GlobalSettingsKVQueriesMixin:
    """Bare-DSN adapter for the KV mixin (#187 ConnectSource, #281).

    Store call sites that hold a plain DSN string (tests, CLI entry points)
    get the same mixin methods without constructing JobQueries:
    ``JobQueriesBase.__init__`` runs ``init_db`` (schema bootstrap under an
    advisory lock) and needs a jobs_dir, neither of which a DSN-only holder
    must trigger. The private DSN field mirrors ``queries/base.py``.
    """
    kv = GlobalSettingsKVQueriesMixin.__new__(GlobalSettingsKVQueriesMixin)
    kv._path = dsn  # data-layer-private field (see queries/base.py)
    return kv
