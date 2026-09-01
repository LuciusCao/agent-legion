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
